"""Tests des contraintes de la table `one_time_tokens`."""

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.anyio

VALID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
TOKEN_HASH = "b" * 64
INSUFFICIENT_PRIVILEGE = "42501"
INSERT_TOKEN = text(
    "INSERT INTO one_time_tokens (user_id, purpose, token_hash, expires_at, used_at) "
    "VALUES (:user_id, :purpose, :token_hash, now() + :lifetime, now() + :used)"
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


# requires : les arguments enfreignent une contrainte de la table.
# assigns  : tente une insertion dans une transaction annulée à la fin du test.
# ensures  : PostgreSQL refuse la ligne, et c'est bien la contrainte attendue qui agit. Les durées
#            maximales tiennent donc même si l'application était mal réglée.
@pytest.mark.parametrize(
    ("purpose", "token_hash", "lifetime", "used", "constraint"),
    [
        ("autre", TOKEN_HASH, timedelta(minutes=5), None, "ck_one_time_tokens_purpose_known"),
        (
            "password_reset",
            "pas-une-empreinte",
            timedelta(minutes=5),
            None,
            "ck_one_time_tokens_token_hash_sha256_hex",
        ),
        (
            "password_reset",
            TOKEN_HASH,
            timedelta(seconds=-1),
            None,
            "ck_one_time_tokens_expires_after_creation",
        ),
        (
            "password_reset",
            TOKEN_HASH,
            timedelta(minutes=31),
            None,
            "ck_one_time_tokens_password_reset_max_lifetime",
        ),
        (
            "email_verification",
            TOKEN_HASH,
            timedelta(hours=73),
            None,
            "ck_one_time_tokens_email_verification_max_lifetime",
        ),
        (
            "password_reset",
            TOKEN_HASH,
            timedelta(minutes=5),
            timedelta(seconds=-10),
            "ck_one_time_tokens_used_after_creation",
        ),
    ],
)
async def test_database_rejects_invalid_tokens(
    db_session: AsyncSession,
    purpose: str,
    token_hash: str,
    lifetime: timedelta,
    used: timedelta | None,
    constraint: str,
) -> None:
    account_id = await insert_account(db_session)
    values = {
        "user_id": account_id,
        "purpose": purpose,
        "token_hash": token_hash,
        "lifetime": lifetime,
        "used": used,
    }

    with pytest.raises(IntegrityError) as error:
        await db_session.execute(INSERT_TOKEN, values)

    driver_error = error.value.orig.__cause__ if error.value.orig is not None else None
    assert getattr(driver_error, "constraint_name", None) == constraint


# requires : `db_session` est une session isolée.
# assigns  : insère un compte et un jeton, puis supprime le compte, dans une transaction annulée.
# ensures  : supprimer un compte supprime ses jetons (clé étrangère en cascade).
async def test_deleting_account_deletes_its_tokens(db_session: AsyncSession) -> None:
    account_id = await insert_account(db_session)
    await db_session.execute(
        INSERT_TOKEN,
        {
            "user_id": account_id,
            "purpose": "email_verification",
            "token_hash": TOKEN_HASH,
            "lifetime": timedelta(hours=24),
            "used": None,
        },
    )

    await db_session.execute(text("DELETE FROM users WHERE id = :id"), {"id": account_id})

    count = (await db_session.execute(text("SELECT count(*) FROM one_time_tokens"))).scalar_one()
    assert count == 0


# requires : `statement` modifie la structure ou vide la table `one_time_tokens`.
# assigns  : tente l'opération, refusée par PostgreSQL.
# ensures  : le rôle applicatif ne peut ni supprimer, ni vider, ni affaiblir la table.
@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE one_time_tokens",
        "TRUNCATE one_time_tokens",
        "ALTER TABLE one_time_tokens "
        "DROP CONSTRAINT ck_one_time_tokens_password_reset_max_lifetime",
    ],
)
async def test_application_role_cannot_alter_one_time_tokens(
    statement: str, app_engine: AsyncEngine
) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))

    assert getattr(error.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE
