"""Règles métier des tâches. `user_id` vient toujours du jeton, jamais de la requête."""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tasks import repository
from app.modules.tasks.models import Task, TaskDay
from app.modules.tasks.repository import DayPlacement
from app.modules.tasks.schemas import TaskCreate, TaskDaySlot, TaskUpdate


class TaskNotFoundError(Exception):
    """Tâche ou jour inexistant, ou appartenant à un autre utilisateur : on ne dit pas lequel."""


class LastDayError(Exception):
    """Retirer ce jour laisserait la tâche sans aucun jour."""


@dataclass(frozen=True)
class TaskView:
    """Tâche et ses jours, lus pour leur propriétaire."""

    task: Task
    days: list[TaskDay]


# requires : `user_id` vient du jeton d'accès.
# assigns  : rien (lecture seule).
# ensures  : renvoie la tâche et ses jours ; lève `TaskNotFoundError` si elle n'appartient pas à
#            `user_id`.
async def _load(session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskView:
    task = await repository.get_task(session, user_id=user_id, task_id=task_id)
    if task is None:
        raise TaskNotFoundError
    days = await repository.list_task_days(session, user_id=user_id, task_id=task_id)
    return TaskView(task=task, days=days)


# requires : `user_id` vient du jeton d'accès, `data` est validé (1 à 31 jours distincts).
# assigns  : insère la tâche et ses jours, puis valide la transaction.
# ensures  : renvoie la tâche créée et ses jours.
async def create_task(session: AsyncSession, *, user_id: uuid.UUID, data: TaskCreate) -> TaskView:
    task = await repository.insert_task(
        session, user_id=user_id, title=data.title, description=data.description
    )
    await repository.insert_task_days(
        session,
        user_id=user_id,
        task_id=task.id,
        placements=[
            DayPlacement(entry.day, entry.start_hour, entry.duration_hours) for entry in data.days
        ],
    )
    view = await _load(session, user_id=user_id, task_id=task.id)
    await session.commit()
    return view


# requires : `user_id` vient du jeton d'accès.
# assigns  : rien (lecture seule).
# ensures  : renvoie la tâche et ses jours ; lève `TaskNotFoundError` sinon.
async def read_task(session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> TaskView:
    return await _load(session, user_id=user_id, task_id=task_id)


# requires : `user_id` vient du jeton d'accès, `data` modifie au moins un champ.
# assigns  : modifie la tâche, puis valide la transaction.
# ensures  : renvoie la tâche modifiée ; lève `TaskNotFoundError` sans rien modifier sinon.
async def update_task(
    session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, data: TaskUpdate
) -> TaskView:
    # Liste blanche : seuls ces champs, et seulement s'ils ont été envoyés, sont modifiés.
    changes: dict[str, str] = {
        name: value
        for name in ("title", "description")
        if name in data.model_fields_set and isinstance(value := getattr(data, name), str)
    }
    task = await repository.update_task(session, user_id=user_id, task_id=task_id, changes=changes)
    if task is None:
        raise TaskNotFoundError
    view = await _load(session, user_id=user_id, task_id=task_id)
    await session.commit()
    return view


# requires : `user_id` vient du jeton d'accès.
# assigns  : supprime la tâche et ses jours, puis valide la transaction.
# ensures  : la tâche n'existe plus ; lève `TaskNotFoundError` sans rien supprimer sinon.
async def delete_task(session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
    if not await repository.delete_task(session, user_id=user_id, task_id=task_id):
        raise TaskNotFoundError
    await session.commit()


# requires : `user_id` vient du jeton d'accès, `slot` est validé.
# assigns  : crée ou remplace le jour de la tâche, puis valide la transaction.
# ensures  : renvoie le jour enregistré ; lève `TaskNotFoundError` sans rien écrire si la tâche
#            n'appartient pas à `user_id`.
async def put_task_day(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    day: date,
    slot: TaskDaySlot,
) -> TaskDay:
    task_day = await repository.upsert_task_day(
        session,
        user_id=user_id,
        task_id=task_id,
        placement=DayPlacement(day, slot.start_hour, slot.duration_hours),
    )
    if task_day is None:
        raise TaskNotFoundError
    await session.commit()
    return task_day


# requires : `user_id` vient du jeton d'accès.
# assigns  : verrouille la tâche, supprime le jour, puis valide la transaction.
# ensures  : le jour est retiré ; lève `TaskNotFoundError` si la tâche ou le jour n'existe pas pour
#            `user_id`, ou `LastDayError` (rien n'est supprimé) s'il s'agit du dernier jour.
async def remove_task_day(
    session: AsyncSession, *, user_id: uuid.UUID, task_id: uuid.UUID, day: date
) -> None:
    # Sans ce verrou, deux retraits simultanés des deux derniers jours compteraient chacun « deux
    # jours », et laisseraient tous les deux la tâche vide.
    if not await repository.lock_task(session, user_id=user_id, task_id=task_id):
        raise TaskNotFoundError
    remaining = await repository.count_task_days(session, user_id=user_id, task_id=task_id)
    if not await repository.delete_task_day(session, user_id=user_id, task_id=task_id, day=day):
        raise TaskNotFoundError
    if remaining <= 1:
        await session.rollback()
        raise LastDayError
    await session.commit()
