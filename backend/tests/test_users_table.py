"""Tests des contraintes de la table `users` : la base refuse ce que la validation refuse."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.anyio

VALID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"
INSERT_USER = text(
    "INSERT INTO users (email, password_hash, token_version) "
    "VALUES (:email, :password_hash, :token_version)"
)
INSUFFICIENT_PRIVILEGE = "42501"
STRING_TOO_LONG = "22001"


# requires : `session` est une session isolée.
# assigns  : insère une ligne dans une transaction annulée à la fin du test.
# ensures  : renvoie le nom de la contrainte violée par cette insertion, ou `None`.
async def violated_constraint(
    session: AsyncSession, email: str, password_hash: str = VALID_HASH, token_version: int = 0
) -> str | None:
    values = {"email": email, "password_hash": password_hash, "token_version": token_version}
    with pytest.raises(IntegrityError) as error:
        await session.execute(INSERT_USER, values)
    driver_error = error.value.orig.__cause__ if error.value.orig is not None else None
    constraint: str | None = getattr(driver_error, "constraint_name", None)
    return constraint


# requires : `db_session` est une session isolée.
# assigns  : insère des lignes dans une transaction annulée à la fin du test.
# ensures  : deux adresses qui ne diffèrent que par la casse ne peuvent pas coexister.
async def test_email_is_unique_regardless_of_case(db_session: AsyncSession) -> None:
    await db_session.execute(
        INSERT_USER, {"email": "alice@exemple.org", "password_hash": VALID_HASH, "token_version": 0}
    )

    assert await violated_constraint(db_session, "ALICE@exemple.org") == "uq_users_email_lower"


# requires : les arguments enfreignent une contrainte de la table.
# assigns  : tente une insertion dans une transaction annulée à la fin du test.
# ensures  : PostgreSQL refuse la ligne, même si la validation de l'API était contournée.
@pytest.mark.parametrize(
    ("email", "password_hash", "token_version", "constraint"),
    [
        ("a@", VALID_HASH, 0, "ck_users_email_length"),
        ("ali\nce@exemple.org", VALID_HASH, 0, "ck_users_email_no_control_characters"),
        ("bob@exemple.org", "mot-de-passe-en-clair", 0, "ck_users_password_hash_argon2id"),
        ("bob@exemple.org", "$2b$12$" + "x" * 53, 0, "ck_users_password_hash_argon2id"),
        (
            "bob@exemple.org",
            "$argon2i$v=19$m=65536,t=3,p=4$x$y",
            0,
            "ck_users_password_hash_argon2id",
        ),
        ("bob@exemple.org", VALID_HASH, -1, "ck_users_token_version_non_negative"),
    ],
)
async def test_database_rejects_invalid_rows(
    db_session: AsyncSession, email: str, password_hash: str, token_version: int, constraint: str
) -> None:
    assert await violated_constraint(db_session, email, password_hash, token_version) == constraint


# requires : `db_session` est une session isolée.
# assigns  : tente une insertion dans une transaction annulée à la fin du test.
# ensures  : une adresse de plus de 254 caractères est refusée par le type de la colonne.
async def test_database_rejects_too_long_email(db_session: AsyncSession) -> None:
    values = {"email": "a" * 250 + "@x.fr", "password_hash": VALID_HASH, "token_version": 0}

    with pytest.raises(DBAPIError) as error:
        await db_session.execute(INSERT_USER, values)

    assert getattr(error.value.orig, "sqlstate", None) == STRING_TOO_LONG


# requires : `statement` modifie la structure ou vide la table `users`.
# assigns  : tente l'opération, refusée par PostgreSQL.
# ensures  : le rôle applicatif ne peut ni supprimer, ni vider, ni affaiblir la table `users`.
@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE users",
        "TRUNCATE users",
        "ALTER TABLE users DROP CONSTRAINT ck_users_password_hash_argon2id",
        "DROP INDEX uq_users_email_lower",
    ],
)
async def test_application_role_cannot_alter_users_table(
    statement: str, app_engine: AsyncEngine
) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))

    assert getattr(error.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE
