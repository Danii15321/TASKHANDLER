"""Tests en base de la grille : contraintes de statut et d'exclusion, index utilisés."""

import json
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

VALID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
INSERT_DAY = text(
    "INSERT INTO task_days (task_id, user_id, day, start_hour, duration_hours, status, "
    "completed_at) VALUES (:task_id, :user_id, :day, :start, :duration, :status, "
    "CASE WHEN :completed THEN now() END)"
)


# requires : `session` est une session isolée.
# assigns  : insère un compte et deux tâches dans une transaction annulée à la fin du test.
# ensures  : renvoie l'identifiant du compte et ceux de ses deux tâches.
async def owner_with_tasks(session: AsyncSession) -> tuple[str, str, str]:
    user_id: str = (
        await session.execute(
            text(
                "INSERT INTO users (email, password_hash) VALUES ('bob@exemple.org', :h) "
                "RETURNING id"
            ),
            {"h": VALID_HASH},
        )
    ).scalar_one()
    task_ids = []
    for _ in range(2):
        task_ids.append(
            (
                await session.execute(
                    text(
                        "INSERT INTO tasks (user_id, title, description) "
                        "VALUES (:u, 'T', 'D') RETURNING id"
                    ),
                    {"u": user_id},
                )
            ).scalar_one()
        )
    return user_id, str(task_ids[0]), str(task_ids[1])


# requires : `error` vient d'une violation de contrainte.
# assigns  : rien.
# ensures  : renvoie le nom de la contrainte violée.
def constraint_of(error: IntegrityError) -> str | None:
    driver_error = error.orig.__cause__ if error.orig is not None else None
    name: str | None = getattr(driver_error, "constraint_name", None)
    return name


# requires : les valeurs enfreignent une contrainte de statut.
# assigns  : tente une insertion dans une transaction annulée à la fin du test.
# ensures  : statut inconnu, `done` sans date, ou date sans `done` : refusés par la base.
@pytest.mark.parametrize(
    ("status", "completed", "constraint"),
    [
        ("fini", False, "ck_task_days_status_known"),
        ("done", False, "ck_task_days_completed_at_consistent"),
        ("todo", True, "ck_task_days_completed_at_consistent"),
    ],
)
async def test_database_enforces_status_rules(
    db_session: AsyncSession, status: str, completed: bool, constraint: str
) -> None:
    user_id, task_id, _ = await owner_with_tasks(db_session)
    values = {
        "task_id": task_id,
        "user_id": user_id,
        "day": date(2026, 11, 16),
        "start": 9,
        "duration": 1,
        "status": status,
        "completed": completed,
    }

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(INSERT_DAY, values)

    assert constraint_of(error.value) == constraint


# requires : `db_session` est une session isolée.
# assigns  : tente une priorité inconnue dans une transaction annulée à la fin du test.
# ensures  : la base refuse une priorité hors de la liste.
async def test_database_enforces_priority(db_session: AsyncSession) -> None:
    user_id, _, _ = await owner_with_tasks(db_session)

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            text(
                "INSERT INTO tasks (user_id, title, description, priority) "
                "VALUES (:u, 'T', 'D', 'urgent')"
            ),
            {"u": user_id},
        )

    assert constraint_of(error.value) == "ck_tasks_priority_known"


# requires : `db_session` est une session isolée.
# assigns  : insère deux créneaux qui se chevauchent, directement en SQL.
# ensures  : la contrainte d'exclusion refuse le chevauchement même sans passer par l'API, et
#            l'autorise quand l'un des deux jours est annulé.
async def test_exclusion_constraint_in_database(db_session: AsyncSession) -> None:
    user_id, first, second = await owner_with_tasks(db_session)
    base = {"user_id": user_id, "day": date(2026, 11, 16), "completed": False}
    await db_session.execute(
        INSERT_DAY, {**base, "task_id": first, "start": 9, "duration": 3, "status": "todo"}
    )

    await db_session.execute(text("SAVEPOINT avant_chevauchement"))
    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            INSERT_DAY, {**base, "task_id": second, "start": 11, "duration": 1, "status": "todo"}
        )
    assert constraint_of(error.value) == "ex_task_days_no_overlap"
    await db_session.execute(text("ROLLBACK TO SAVEPOINT avant_chevauchement"))

    await db_session.execute(
        INSERT_DAY, {**base, "task_id": second, "start": 11, "duration": 1, "status": "cancelled"}
    )


# requires : `query` est l'une des deux requêtes réelles de la phase.
# assigns  : désactive le parcours séquentiel le temps de la transaction du test.
# ensures  : PostgreSQL sert la requête par l'index composite créé pour elle. Sans données, il
#            préférerait parcourir la table : désactiver ce parcours révèle l'index choisi.
@pytest.mark.parametrize(
    ("query", "index"),
    [
        (
            "SELECT id FROM tasks WHERE user_id = gen_random_uuid() "
            "ORDER BY created_at DESC, id DESC LIMIT 21",
            "ix_tasks_user_created_id",
        ),
        (
            "SELECT task_id FROM task_days WHERE user_id = gen_random_uuid() "
            "AND day BETWEEN DATE '2026-11-16' AND DATE '2026-11-22' ORDER BY day, start_hour",
            "ix_task_days_user_day_start",
        ),
    ],
)
async def test_real_queries_use_their_index(
    db_session: AsyncSession, query: str, index: str
) -> None:
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))

    plan = (await db_session.execute(text(f"EXPLAIN (FORMAT JSON) {query}"))).scalar_one()

    assert index in json.dumps(plan)
