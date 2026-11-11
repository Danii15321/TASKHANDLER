"""Tests des contraintes des tables `tasks` et `task_days` : la base refuse ce que l'API refuse."""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.tasks.models import (
    DESCRIPTION_MAX_LENGTH,
    FIRST_ALLOWED_DAY,
    LAST_ALLOWED_DAY,
    TITLE_MAX_LENGTH,
)
from tests.conftest import act_as

pytestmark = pytest.mark.anyio

VALID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
INSUFFICIENT_PRIVILEGE = "42501"
INSERT_TASK = text(
    "INSERT INTO tasks (user_id, title, description) VALUES (:user_id, :title, :description) "
    "RETURNING id"
)
INSERT_DAY = text(
    "INSERT INTO task_days (task_id, user_id, day, start_hour, duration_hours) "
    "VALUES (:task_id, :user_id, :day, :start_hour, :duration_hours)"
)


# requires : `session` est une session isolée, `email` une adresse libre.
# assigns  : insère un compte dans une transaction annulée à la fin du test, et le déclare comme
#            utilisateur courant pour la RLS.
# ensures  : renvoie l'identifiant du compte créé.
async def insert_account(session: AsyncSession, email: str) -> str:
    account_id: str = (
        await session.execute(
            text("INSERT INTO users (email, password_hash) VALUES (:email, :hash) RETURNING id"),
            {"email": email, "hash": VALID_HASH},
        )
    ).scalar_one()
    # Le dernier compte créé devient l'utilisateur courant, comme après une connexion.
    await act_as(session, account_id)
    return account_id


# requires : `session` est une session isolée, `user_id` un compte existant.
# assigns  : insère une tâche valide.
# ensures  : renvoie l'identifiant de la tâche.
async def insert_task(session: AsyncSession, user_id: str) -> str:
    task_id: str = (
        await session.execute(
            INSERT_TASK, {"user_id": user_id, "title": "Titre", "description": "Description"}
        )
    ).scalar_one()
    return task_id


# requires : `error` vient d'une violation de contrainte.
# assigns  : rien.
# ensures  : renvoie le nom de la contrainte violée, lu dans l'erreur du pilote.
def constraint_of(error: IntegrityError) -> str | None:
    driver_error = error.orig.__cause__ if error.orig is not None else None
    name: str | None = getattr(driver_error, "constraint_name", None)
    return name


# requires : `title` et `description` enfreignent une contrainte de `tasks`.
# assigns  : tente une insertion dans une transaction annulée à la fin du test.
# ensures  : PostgreSQL refuse la ligne, avec la contrainte attendue.
@pytest.mark.parametrize(
    ("title", "description", "constraint"),
    [
        ("   ", "Description", "ck_tasks_title_not_blank"),
        ("", "Description", "ck_tasks_title_length"),
        ("ligne\nsuivante", "Description", "ck_tasks_title_no_control_characters"),
        ("Titre", " \n\t ", "ck_tasks_description_not_blank"),
        ("Titre", "x" * (DESCRIPTION_MAX_LENGTH + 1), "ck_tasks_description_length"),
        ("Titre", "échappement\x1b[31m", "ck_tasks_description_no_control_characters"),
    ],
)
async def test_database_rejects_invalid_tasks(
    db_session: AsyncSession, title: str, description: str, constraint: str
) -> None:
    user_id = await insert_account(db_session, "bob@exemple.org")

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(
            INSERT_TASK, {"user_id": user_id, "title": title, "description": description}
        )

    assert constraint_of(error.value) == constraint


# requires : les arguments enfreignent une contrainte de `task_days`.
# assigns  : tente une insertion dans une transaction annulée à la fin du test.
# ensures  : PostgreSQL refuse le jour, avec la contrainte attendue.
@pytest.mark.parametrize(
    ("day", "start_hour", "duration_hours", "constraint"),
    [
        (date(2026, 11, 16), -1, 1, "ck_task_days_start_hour_range"),
        (date(2026, 11, 16), 8, 0, "ck_task_days_duration_positive"),
        (date(2026, 11, 16), 20, 5, "ck_task_days_fits_in_day"),
        (FIRST_ALLOWED_DAY - timedelta(days=1), 8, 1, "ck_task_days_day_range"),
        (LAST_ALLOWED_DAY + timedelta(days=1), 8, 1, "ck_task_days_day_range"),
    ],
)
async def test_database_rejects_invalid_days(
    db_session: AsyncSession, day: date, start_hour: int, duration_hours: int, constraint: str
) -> None:
    user_id = await insert_account(db_session, "bob@exemple.org")
    task_id = await insert_task(db_session, user_id)
    values = {
        "task_id": task_id,
        "user_id": user_id,
        "day": day,
        "start_hour": start_hour,
        "duration_hours": duration_hours,
    }

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(INSERT_DAY, values)

    assert constraint_of(error.value) == constraint


# requires : `db_session` est une session isolée.
# assigns  : crée deux comptes et une tâche, dans une transaction annulée à la fin du test.
# ensures  : critère de la roadmap : un jour ne peut pas rattacher la tâche d'Alice au compte de
#            Bob. La clé étrangère composite le refuse, même si l'application se trompait.
async def test_day_cannot_mix_owners(db_session: AsyncSession) -> None:
    alice = await insert_account(db_session, "alice@exemple.org")
    bob = await insert_account(db_session, "bob@exemple.org")
    await act_as(db_session, alice)
    alice_task = await insert_task(db_session, alice)
    # Bob tente de rattacher un jour à la tâche d'Alice : la RLS laisse passer sa propre ligne,
    # c'est la clé étrangère composite qui doit refuser le mélange des propriétaires.
    await act_as(db_session, bob)
    values = {
        "task_id": alice_task,
        "user_id": bob,
        "day": date(2026, 11, 16),
        "start_hour": 8,
        "duration_hours": 1,
    }

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(INSERT_DAY, values)

    assert constraint_of(error.value) == "fk_task_days_task_owner"


# requires : `db_session` est une session isolée.
# assigns  : insère deux fois le même jour, dans une transaction annulée à la fin du test.
# ensures  : une tâche n'a qu'un créneau par jour (clé primaire `task_id`, `day`).
async def test_one_slot_per_day(db_session: AsyncSession) -> None:
    user_id = await insert_account(db_session, "bob@exemple.org")
    task_id = await insert_task(db_session, user_id)
    values = {
        "task_id": task_id,
        "user_id": user_id,
        "day": date(2026, 11, 16),
        "start_hour": 8,
        "duration_hours": 1,
    }
    await db_session.execute(INSERT_DAY, values)

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(INSERT_DAY, {**values, "start_hour": 10})

    assert constraint_of(error.value) == "pk_task_days"


# requires : `db_session` est une session isolée.
# assigns  : insère les valeurs limites, dans une transaction annulée à la fin du test.
# ensures  : la base accepte exactement les bornes des schémas : les constantes Python et les
#            contraintes écrites en toutes lettres restent d'accord.
async def test_database_accepts_schema_boundaries(db_session: AsyncSession) -> None:
    user_id = await insert_account(db_session, "bob@exemple.org")
    task_id: str = (
        await db_session.execute(
            INSERT_TASK,
            {
                "user_id": user_id,
                "title": "t" * TITLE_MAX_LENGTH,
                "description": "d" * DESCRIPTION_MAX_LENGTH,
            },
        )
    ).scalar_one()
    for day, start_hour, duration_hours in [
        (FIRST_ALLOWED_DAY, 0, 24),
        (LAST_ALLOWED_DAY, 23, 1),
    ]:
        await db_session.execute(
            INSERT_DAY,
            {
                "task_id": task_id,
                "user_id": user_id,
                "day": day,
                "start_hour": start_hour,
                "duration_hours": duration_hours,
            },
        )


# requires : `db_session` est une session isolée.
# assigns  : crée un compte, une tâche et un jour, puis supprime le compte.
# ensures  : supprimer un compte supprime ses tâches et leurs jours (cascade).
async def test_deleting_account_deletes_tasks(db_session: AsyncSession) -> None:
    user_id = await insert_account(db_session, "bob@exemple.org")
    task_id = await insert_task(db_session, user_id)
    await db_session.execute(
        INSERT_DAY,
        {
            "task_id": task_id,
            "user_id": user_id,
            "day": date(2026, 11, 16),
            "start_hour": 8,
            "duration_hours": 1,
        },
    )

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

    remaining = (
        await db_session.execute(
            text("SELECT (SELECT count(*) FROM tasks) + (SELECT count(*) FROM task_days)")
        )
    ).scalar_one()
    assert remaining == 0


# requires : `statement` modifie la structure ou vide une table des tâches.
# assigns  : tente l'opération, refusée par PostgreSQL.
# ensures  : le rôle applicatif ne peut ni supprimer, ni vider, ni affaiblir ces tables.
@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE tasks",
        "TRUNCATE task_days",
        "ALTER TABLE task_days DROP CONSTRAINT fk_task_days_task_owner",
    ],
)
async def test_application_role_cannot_alter_task_tables(
    statement: str, app_engine: AsyncEngine
) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))

    assert getattr(error.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE
