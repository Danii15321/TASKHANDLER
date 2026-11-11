"""Accès aux données des tâches.

Règle sans exception : chaque requête porte `user_id` dans son `WHERE`, y compris `UPDATE` et
`DELETE`. La tâche d'un autre utilisateur est donc introuvable, et jamais lue pour être refusée
ensuite en Python (OWASP API1).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import (
    Date,
    Row,
    SmallInteger,
    Uuid,
    and_,
    delete,
    exists,
    func,
    insert,
    literal,
    select,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tasks.models import STATUS_DONE, Task, TaskDay


@dataclass(frozen=True)
class DayPlacement:
    """Place d'une tâche dans un jour, déjà validée par les schémas."""

    day: date
    start_hour: int
    duration_hours: int


# requires : `title`, `description` et `priority` sont nettoyés et validés.
# assigns  : insère une tâche, sans valider la transaction.
# ensures  : renvoie la tâche créée, avec les valeurs calculées par PostgreSQL.
async def insert_task(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    title: str,
    description: str,
    priority: str,
) -> Task:
    statement = (
        insert(Task)
        .values(user_id=user_id, title=title, description=description, priority=priority)
        .returning(Task)
    )
    return (await session.execute(statement)).scalar_one()


# requires : la tâche `task_id` appartient à `user_id` et vient d'être créée ; `placements` est
#            non vide et sans jour répété.
# assigns  : insère les jours de la tâche, sans valider la transaction.
# ensures  : les jours sont enregistrés ; la clé étrangère composite refuserait tout jour dont le
#            couple (tâche, propriétaire) n'existe pas.
async def insert_task_days(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    placements: list[DayPlacement],
) -> None:
    await session.execute(
        insert(TaskDay).values(
            [
                {
                    "task_id": task_id,
                    "user_id": user_id,
                    "day": placement.day,
                    "start_hour": placement.start_hour,
                    "duration_hours": placement.duration_hours,
                }
                for placement in placements
            ]
        )
    )


# requires : rien.
# assigns  : rien (lecture seule).
# ensures  : renvoie la tâche si elle appartient à `user_id`, sinon `None`.
async def get_task(session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> Task | None:
    statement = select(Task).where(Task.id == task_id, Task.user_id == user_id)
    return (await session.execute(statement)).scalar_one_or_none()


# requires : rien.
# assigns  : rien (lecture seule).
# ensures  : renvoie les jours de la tâche, du plus ancien au plus récent, s'ils appartiennent à
#            `user_id` ; une liste vide sinon.
async def list_task_days(
    session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID
) -> list[TaskDay]:
    statement = (
        select(TaskDay)
        .where(TaskDay.task_id == task_id, TaskDay.user_id == user_id)
        .order_by(TaskDay.day)
    )
    return list((await session.execute(statement)).scalars())


# requires : `changes` ne contient que des colonnes modifiables (`title`, `description`,
#            `priority`), validées.
# assigns  : modifie la tâche, sans valider la transaction.
# ensures  : renvoie la tâche modifiée si elle appartient à `user_id`, sinon `None` sans rien
#            modifier.
async def update_task(
    session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, changes: dict[str, str]
) -> Task | None:
    statement = (
        update(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .values(**changes)
        .returning(Task)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(statement)).scalar_one_or_none()


# requires : rien.
# assigns  : supprime la tâche et, en cascade, ses jours, sans valider la transaction.
# ensures  : renvoie `True` si une tâche de `user_id` a été supprimée, `False` sinon.
async def delete_task(session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> bool:
    statement = (
        delete(Task)
        .where(Task.id == task_id, Task.user_id == user_id)
        .returning(Task.id)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


# requires : `placement` est validé.
# assigns  : crée ou remplace le jour de la tâche, sans valider la transaction.
# ensures  : renvoie le jour enregistré si la tâche appartient à `user_id`, sinon `None` sans rien
#            écrire. Le propriétaire est vérifié par la tâche parente, dans la même instruction :
#            l'insertion n'a lieu que si `EXISTS (tâche de cet utilisateur)`.
async def upsert_task_day(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    placement: DayPlacement,
) -> TaskDay | None:
    owned_task = exists().where(Task.id == task_id, Task.user_id == user_id)
    row = select(
        literal(task_id, Uuid),
        literal(placement.day, Date),
        literal(user_id, Uuid),
        literal(placement.start_hour, SmallInteger),
        literal(placement.duration_hours, SmallInteger),
    ).where(owned_task)
    insertion = pg_insert(TaskDay).from_select(
        ["task_id", "day", "user_id", "start_hour", "duration_hours"], row
    )
    statement = insertion.on_conflict_do_update(
        index_elements=[TaskDay.task_id, TaskDay.day],
        set_={
            "start_hour": insertion.excluded.start_hour,
            "duration_hours": insertion.excluded.duration_hours,
        },
        # Ceinture et bretelles : la ligne existante doit, elle aussi, appartenir au demandeur.
        where=TaskDay.user_id == user_id,
    ).returning(TaskDay)
    return (await session.execute(statement)).scalar_one_or_none()


# requires : une transaction est ouverte.
# assigns  : verrouille la ligne de la tâche jusqu'à la fin de la transaction.
# ensures  : renvoie `True` si la tâche appartient à `user_id` (et la verrouille), `False` sinon.
#            Deux retraits simultanés de jours de la même tâche passent ainsi l'un après l'autre.
async def lock_task(session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> bool:
    statement = select(Task.id).where(Task.id == task_id, Task.user_id == user_id).with_for_update()
    return (await session.execute(statement)).scalar_one_or_none() is not None


# requires : rien.
# assigns  : rien (lecture seule).
# ensures  : renvoie le nombre de jours de la tâche appartenant à `user_id`.
async def count_task_days(session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> int:
    statement = select(func.count()).where(TaskDay.task_id == task_id, TaskDay.user_id == user_id)
    return int((await session.execute(statement)).scalar_one())


# requires : rien.
# assigns  : supprime un jour de la tâche, sans valider la transaction.
# ensures  : renvoie `True` si un jour de `user_id` a été supprimé, `False` sinon.
async def delete_task_day(
    session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, day: date
) -> bool:
    statement = (
        delete(TaskDay)
        .where(TaskDay.task_id == task_id, TaskDay.user_id == user_id, TaskDay.day == day)
        .returning(TaskDay.day)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(statement)).scalar_one_or_none() is not None


# requires : `status` est un statut connu.
# assigns  : change le statut du jour, et `completed_at` en conséquence, sans valider.
# ensures  : renvoie le jour modifié s'il appartient à `user_id`, sinon `None`. `completed_at` est
#            daté au passage à `done` (et conservé s'il l'était déjà), effacé sinon : le client ne
#            le fixe jamais, et la contrainte de la table garantit cette cohérence.
async def update_task_day_status(
    session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, day: date, status: str
) -> TaskDay | None:
    completed_at = (
        func.coalesce(TaskDay.completed_at, func.now()) if status == STATUS_DONE else None
    )
    statement = (
        update(TaskDay)
        .where(TaskDay.task_id == task_id, TaskDay.user_id == user_id, TaskDay.day == day)
        .values(status=status, completed_at=completed_at)
        .returning(TaskDay)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(statement)).scalar_one_or_none()


# requires : `limit` est borné par le schéma ; `after` vient d'un curseur décodé, ou `None`.
# assigns  : rien (lecture seule).
# ensures  : renvoie jusqu'à `limit` tâches de `user_id`, des plus récentes aux plus anciennes,
#            situées après `after`. Le tri sur (`created_at`, `id`) est total : aucune tâche n'est
#            sautée ni répétée, même à date de création égale.
async def list_tasks(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[Task]:
    statement = select(Task).where(Task.user_id == user_id)
    if after is not None:
        statement = statement.where(tuple_(Task.created_at, Task.id) < tuple_(*after))
    statement = statement.order_by(Task.created_at.desc(), Task.id.desc()).limit(limit)
    return list((await session.execute(statement)).scalars())


# requires : `start` ≤ `end` ; `statuses` et `priorities` sont des valeurs connues ; `limit` ≥ 1.
# assigns  : rien (lecture seule).
# ensures  : renvoie au plus `limit` cases de la grille de `user_id` sur la période, triées par
#            jour puis heure. Seules les colonnes de la grille sont lues : jamais la description.
async def planning_entries(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    start: date,
    end: date,
    statuses: list[str],
    priorities: list[str],
    limit: int,
) -> list[Row[uuid.UUID, str, str, date, int, int, str]]:
    statement = (
        select(
            TaskDay.task_id,
            Task.title,
            Task.priority,
            TaskDay.day,
            TaskDay.start_hour,
            TaskDay.duration_hours,
            TaskDay.status,
        )
        # Jointure sur le couple (tâche, propriétaire), et filtre par propriétaire des deux côtés.
        .join(Task, and_(Task.id == TaskDay.task_id, Task.user_id == TaskDay.user_id))
        .where(
            TaskDay.user_id == user_id,
            Task.user_id == user_id,
            TaskDay.day.between(start, end),
        )
        .order_by(TaskDay.day, TaskDay.start_hour, TaskDay.task_id)
        .limit(limit)
    )
    if statuses:
        statement = statement.where(TaskDay.status.in_(statuses))
    if priorities:
        statement = statement.where(Task.priority.in_(priorities))
    return list((await session.execute(statement)).all())
