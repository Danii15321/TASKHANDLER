"""Tests de la sécurité au niveau des lignes (RLS) : PostgreSQL filtre lui-même par propriétaire."""

from typing import Any, cast

import pytest
from sqlalchemy import CursorResult, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.tasks.models import Task
from tests.conftest import act_as

pytestmark = pytest.mark.anyio

VALID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
INSUFFICIENT_PRIVILEGE = "42501"


# requires : `session` est une session isolée.
# assigns  : crée Alice et Bob, chacun avec une tâche, dans une transaction annulée à la fin.
# ensures  : renvoie les identifiants d'Alice et de Bob.
async def alice_and_bob(session: AsyncSession) -> tuple[str, str]:
    accounts = []
    for email in ("alice@exemple.org", "bob@exemple.org"):
        account_id: str = (
            await session.execute(
                text("INSERT INTO users (email, password_hash) VALUES (:e, :h) RETURNING id"),
                {"e": email, "h": VALID_HASH},
            )
        ).scalar_one()
        await act_as(session, account_id)
        await session.execute(
            text("INSERT INTO tasks (user_id, title, description) VALUES (:u, :t, 'D')"),
            {"u": account_id, "t": f"Tâche de {email}"},
        )
        accounts.append(account_id)
    return accounts[0], accounts[1]


# requires : `session` est une session isolée.
# assigns  : rien (lecture seule).
# ensures  : renvoie les titres visibles, sans aucun filtre `WHERE` dans la requête.
async def visible_titles(session: AsyncSession) -> list[str]:
    return list((await session.execute(text("SELECT title FROM tasks ORDER BY title"))).scalars())


# requires : `db_session` est une session isolée.
# assigns  : crée deux comptes et leurs tâches, dans une transaction annulée à la fin du test.
# ensures  : sans aucun `WHERE`, chaque utilisateur ne voit que ses propres tâches, et personne ne
#            voit rien tant qu'aucun utilisateur n'est déclaré.
async def test_rls_filters_queries_without_where(db_session: AsyncSession) -> None:
    alice, bob = await alice_and_bob(db_session)

    await act_as(db_session, alice)
    seen_by_alice = await visible_titles(db_session)
    await act_as(db_session, bob)
    seen_by_bob = await visible_titles(db_session)
    await act_as(db_session, "")
    seen_by_nobody = await visible_titles(db_session)

    assert seen_by_alice == ["Tâche de alice@exemple.org"]
    assert seen_by_bob == ["Tâche de bob@exemple.org"]
    assert seen_by_nobody == []


# requires : `db_session` est une session isolée.
# assigns  : crée deux comptes, dans une transaction annulée à la fin du test.
# ensures  : un oubli de `WHERE user_id` dans une requête ORM serait rattrapé par la base.
async def test_rls_protects_a_forgotten_owner_filter(db_session: AsyncSession) -> None:
    _, bob = await alice_and_bob(db_session)
    await act_as(db_session, bob)

    # Requête volontairement fautive : aucune condition sur le propriétaire.
    tasks = (await db_session.execute(select(Task))).scalars().all()

    assert [task.title for task in tasks] == ["Tâche de bob@exemple.org"]


# requires : `db_session` est une session isolée.
# assigns  : tente d'écrire au nom d'un autre, dans une transaction annulée à la fin du test.
# ensures  : Bob ne peut ni créer une tâche au nom d'Alice, ni modifier ou supprimer la sienne.
async def test_rls_blocks_writes_on_behalf_of_others(db_session: AsyncSession) -> None:
    alice, bob = await alice_and_bob(db_session)
    await act_as(db_session, bob)

    updated = cast(
        CursorResult[Any],
        await db_session.execute(
            text("UPDATE tasks SET title = 'Piraté' WHERE user_id = :a"), {"a": alice}
        ),
    )
    deleted = cast(
        CursorResult[Any],
        await db_session.execute(text("DELETE FROM tasks WHERE user_id = :a"), {"a": alice}),
    )
    assert updated.rowcount == 0
    assert deleted.rowcount == 0

    with pytest.raises(DBAPIError) as error:
        await db_session.execute(
            text("INSERT INTO tasks (user_id, title, description) VALUES (:a, 'T', 'D')"),
            {"a": alice},
        )
    assert getattr(error.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE


# requires : `app_engine` utilise le rôle applicatif.
# assigns  : rien.
# ensures  : le rôle applicatif ne peut pas désactiver la RLS ni supprimer une politique.
@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE tasks DISABLE ROW LEVEL SECURITY",
        "DROP POLICY tasks_owner ON tasks",
        "ALTER TABLE task_days NO FORCE ROW LEVEL SECURITY",
    ],
)
async def test_application_role_cannot_disable_rls(statement: str, app_engine: AsyncEngine) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))

    assert getattr(error.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE


# requires : `db_session` est une session isolée.
# assigns  : lit la configuration des tables, sans rien modifier.
# ensures  : la RLS est bien active sur les deux tables de tâches.
async def test_rls_is_enabled_on_task_tables(db_session: AsyncSession) -> None:
    rows = (
        await db_session.execute(
            text(
                "SELECT relname, relrowsecurity FROM pg_class "
                "WHERE relname IN ('tasks', 'task_days') ORDER BY relname"
            )
        )
    ).all()

    assert [tuple(row) for row in rows] == [("task_days", True), ("tasks", True)]
