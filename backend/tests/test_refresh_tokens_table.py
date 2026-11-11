"""Tests des contraintes de la table `refresh_tokens`."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.anyio

VALID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
TOKEN_HASH = "a" * 64
REVOKED_AT = datetime(2026, 1, 1, tzinfo=UTC)
INSUFFICIENT_PRIVILEGE = "42501"
INSERT_TOKEN = text(
    "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at, "
    "session_expires_at, revoked_at, revocation_reason) "
    "VALUES (:user_id, gen_random_uuid(), :token_hash, now() + :expires, now() + :session, "
    ":revoked_at, :reason)"
)


# requires : `session` est une session isolée.
# assigns  : insère un compte dans une transaction annulée à la fin du test.
# ensures  : renvoie l'identifiant du compte créé.
async def insert_account(session: AsyncSession) -> str:
    account_id: str = (
        await session.execute(
            text(
                "INSERT INTO users (email, password_hash) VALUES ('bob@exemple.org', :hash) "
                "RETURNING id"
            ),
            {"hash": VALID_HASH},
        )
    ).scalar_one()
    return account_id


# requires : `db_session` est une session isolée.
# assigns  : insère un compte et un jeton, puis supprime le compte, dans une transaction annulée.
# ensures  : supprimer un compte supprime toutes ses sessions (clé étrangère en cascade).
async def test_deleting_account_deletes_its_sessions(db_session: AsyncSession) -> None:
    account_id = await insert_account(db_session)
    await db_session.execute(
        INSERT_TOKEN,
        {
            "user_id": account_id,
            "token_hash": TOKEN_HASH,
            "expires": timedelta(days=7),
            "session": timedelta(days=30),
            "revoked_at": None,
            "reason": None,
        },
    )

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": account_id})

    assert (await db_session.execute(text("SELECT count(*) FROM refresh_tokens"))).scalar_one() == 0


# requires : les arguments enfreignent une contrainte de la table.
# assigns  : tente une insertion dans une transaction annulée à la fin du test.
# ensures  : PostgreSQL refuse la ligne, et c'est bien la contrainte attendue qui agit.
@pytest.mark.parametrize(
    ("token_hash", "expires", "session", "revoked_at", "reason", "constraint"),
    [
        (
            "pas-une-empreinte",
            timedelta(days=7),
            timedelta(days=30),
            None,
            None,
            "ck_refresh_tokens_token_hash_sha256_hex",
        ),
        (
            TOKEN_HASH,
            timedelta(seconds=-1),
            timedelta(days=30),
            None,
            None,
            "ck_refresh_tokens_expires_after_creation",
        ),
        (
            TOKEN_HASH,
            timedelta(days=31),
            timedelta(days=30),
            None,
            None,
            "ck_refresh_tokens_within_session_lifetime",
        ),
        (
            TOKEN_HASH,
            timedelta(days=7),
            timedelta(days=30),
            REVOKED_AT,
            None,
            "ck_refresh_tokens_revocation_consistent",
        ),
        (
            TOKEN_HASH,
            timedelta(days=7),
            timedelta(days=30),
            None,
            "rotated",
            "ck_refresh_tokens_revocation_consistent",
        ),
        (
            TOKEN_HASH,
            timedelta(days=7),
            timedelta(days=30),
            REVOKED_AT,
            "inconnue",
            "ck_refresh_tokens_revocation_reason_known",
        ),
    ],
)
async def test_database_rejects_invalid_tokens(
    db_session: AsyncSession,
    token_hash: str,
    expires: timedelta,
    session: timedelta,
    revoked_at: datetime | None,
    reason: str | None,
    constraint: str,
) -> None:
    account_id = await insert_account(db_session)
    values = {
        "user_id": account_id,
        "token_hash": token_hash,
        "expires": expires,
        "session": session,
        "revoked_at": revoked_at,
        "reason": reason,
    }

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(INSERT_TOKEN, values)

    driver_error = error.value.orig.__cause__ if error.value.orig is not None else None
    assert getattr(driver_error, "constraint_name", None) == constraint


# requires : `db_session` est une session isolée.
# assigns  : insère deux jetons dans une transaction annulée à la fin du test.
# ensures  : deux jetons ne peuvent pas avoir la même empreinte.
async def test_token_hash_is_unique(db_session: AsyncSession) -> None:
    account_id = await insert_account(db_session)
    values = {
        "user_id": account_id,
        "token_hash": TOKEN_HASH,
        "expires": timedelta(days=7),
        "session": timedelta(days=30),
        "revoked_at": None,
        "reason": None,
    }
    await db_session.execute(INSERT_TOKEN, values)

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(INSERT_TOKEN, values)

    driver_error = error.value.orig.__cause__ if error.value.orig is not None else None
    assert getattr(driver_error, "constraint_name", None) == "uq_refresh_tokens_token_hash"


# requires : `statement` modifie la structure ou vide la table `refresh_tokens`.
# assigns  : tente l'opération, refusée par PostgreSQL.
# ensures  : le rôle applicatif ne peut ni supprimer, ni vider, ni affaiblir la table.
@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE refresh_tokens",
        "TRUNCATE refresh_tokens",
        "ALTER TABLE refresh_tokens DROP CONSTRAINT ck_refresh_tokens_revocation_reason_known",
    ],
)
async def test_application_role_cannot_alter_refresh_tokens(
    statement: str, app_engine: AsyncEngine
) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))

    assert getattr(error.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE
