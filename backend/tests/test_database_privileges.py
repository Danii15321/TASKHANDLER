"""Tests du moindre privilège : le rôle applicatif ne peut pas modifier le schéma."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.anyio

INSUFFICIENT_PRIVILEGE = "42501"

ROLE_ATTRIBUTES = text(
    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
    "FROM pg_roles WHERE rolname = current_user"
)


# requires : `statement` nécessite un droit que le rôle applicatif n'a pas.
# assigns  : tente une opération sur la base de test, refusée par PostgreSQL.
# ensures  : PostgreSQL refuse l'opération pour cause de droits insuffisants.
@pytest.mark.parametrize(
    "statement",
    [
        "DROP TABLE security_probe",
        "ALTER TABLE security_probe ADD COLUMN intruder TEXT",
        "TRUNCATE security_probe",
        "CREATE TABLE intruder (id INTEGER)",
        "CREATE TEMP TABLE intruder (id INTEGER)",
        "CREATE SCHEMA intruder",
        "SELECT version_num FROM alembic_version",
        "UPDATE alembic_version SET version_num = 'intrus'",
    ],
)
async def test_application_role_cannot_change_schema(
    statement: str, probe_table: str, app_engine: AsyncEngine
) -> None:
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))

    assert getattr(error.value.orig, "sqlstate", None) == INSUFFICIENT_PRIVILEGE


# requires : la table de sonde a été créée par le rôle de migration.
# assigns  : modifie des lignes dans une transaction annulée à la fin du test.
# ensures  : les privilèges par défaut autorisent les quatre opérations sur les données.
async def test_application_role_can_manipulate_data(
    probe_table: str, app_engine: AsyncEngine
) -> None:
    async with app_engine.begin() as connection:
        await connection.execute(text("INSERT INTO security_probe VALUES (1, 'a')"))
        await connection.execute(text("UPDATE security_probe SET label = 'b' WHERE id = 1"))
        label = (await connection.execute(text("SELECT label FROM security_probe"))).scalar_one()
        await connection.execute(text("DELETE FROM security_probe WHERE id = 1"))
        await connection.rollback()

    assert label == "b"


# requires : la table de sonde a été créée par le rôle de migration.
# assigns  : tente de partager la table de sonde, ce que PostgreSQL ignore.
# ensures  : sans option de transmission, le rôle applicatif ne peut céder aucun droit. PostgreSQL
#            émet un simple avertissement : c'est l'absence d'effet qui est vérifiée.
async def test_application_role_cannot_share_its_privileges(
    probe_table: str, app_engine: AsyncEngine
) -> None:
    async with app_engine.begin() as connection:
        await connection.execute(text("GRANT SELECT ON security_probe TO PUBLIC"))
        public_can_read = (
            await connection.execute(
                text("SELECT has_table_privilege('public', 'security_probe', 'SELECT')")
            )
        ).scalar_one()

    assert public_can_read is False


# requires : `engine` est connecté à la base de test.
# assigns  : rien (lecture seule).
# ensures  : renvoie les attributs d'administration du rôle connecté.
async def read_role_attributes(engine: AsyncEngine) -> tuple[bool, ...]:
    async with engine.connect() as connection:
        return tuple((await connection.execute(ROLE_ATTRIBUTES)).one())


# requires : `app_engine` utilise le rôle applicatif.
# assigns  : rien (lecture seule).
# ensures  : le rôle applicatif ne dispose d'aucun attribut d'administration.
async def test_application_role_has_no_administrative_attribute(app_engine: AsyncEngine) -> None:
    assert not any(await read_role_attributes(app_engine))


# requires : `migrator_engine` utilise le rôle de migration.
# assigns  : rien (lecture seule).
# ensures  : le rôle de migration n'est pas non plus superutilisateur.
async def test_migration_role_has_no_administrative_attribute(
    migrator_engine: AsyncEngine,
) -> None:
    assert not any(await read_role_attributes(migrator_engine))
