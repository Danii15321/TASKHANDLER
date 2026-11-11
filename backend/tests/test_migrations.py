"""Tests des migrations : application puis annulation sur la base dédiée aux tests."""

import asyncio

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import DatabaseTestSettings, alembic_config

PRIVILEGE_STATE = text(
    "SELECT "
    "(SELECT count(*) FROM pg_namespace AS n, aclexplode(n.nspacl) AS acl "
    " WHERE n.nspname = 'public' AND acl.grantee = 'taskhandler_app'::regrole) AS app_grants, "
    "has_schema_privilege('public', 'public', 'USAGE') AS public_usage, "
    "(SELECT count(*) FROM pg_default_acl) AS default_privileges, "
    "(SELECT count(*) FROM alembic_version) AS versions, "
    "to_regclass('public.users') IS NOT NULL AS users_table, "
    "to_regclass('public.refresh_tokens') IS NOT NULL AS refresh_tokens_table"
)

# Droit explicite du rôle applicatif, `PUBLIC` exclu, privilèges par défaut (tables et séquences),
# version appliquée et présence des tables `users` et `refresh_tokens`.
STATE_AT_HEAD = (1, False, 2, 1, True, True)
# État initial de PostgreSQL : `PUBLIC` retrouve l'usage du schéma, aucune table ne subsiste.
STATE_AT_BASE = (0, True, 0, 0, False, False)


# requires : `database_url` vise la base de test et le schéma contient `alembic_version`.
# assigns  : rien (lecture seule).
# ensures  : renvoie les droits accordés par les migrations et le nombre de versions appliquées.
def read_migration_state(database_url: str) -> tuple[int, bool, int, int, bool, bool]:
    # requires : aucune boucle d'événements n'est en cours dans ce fil d'exécution.
    # assigns  : ouvre puis ferme une connexion.
    # ensures  : renvoie l'état lu dans PostgreSQL.
    async def read() -> tuple[int, bool, int, int, bool, bool]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                state = (await connection.execute(PRIVILEGE_STATE)).one()
        finally:
            await engine.dispose()
        return (
            int(state.app_grants),
            bool(state.public_usage),
            int(state.default_privileges),
            int(state.versions),
            bool(state.users_table),
            bool(state.refresh_tokens_table),
        )

    return asyncio.run(read())


# requires : la base de test est migrée.
# assigns  : annule puis réapplique toutes les migrations de la base de test.
# ensures  : la montée pose les droits, la descente les retire, et la remontée les rétablit.
def test_migrations_upgrade_and_downgrade_cleanly(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> None:
    database_url = database_settings.migration_database_url.get_secret_value()
    config = alembic_config(database_url)

    try:
        assert read_migration_state(database_url) == STATE_AT_HEAD

        command.downgrade(config, "base")
        assert read_migration_state(database_url) == STATE_AT_BASE
    finally:
        # Les autres tests attendent un schéma à jour, même si une vérification a échoué.
        command.upgrade(config, "head")

    assert read_migration_state(database_url) == STATE_AT_HEAD


# requires : le dossier `migrations/` existe.
# assigns  : rien (lecture seule).
# ensures  : l'historique est linéaire et chaque révision possède une annulation.
def test_migration_history_is_linear_and_reversible() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert len(script.get_heads()) == 1
    for revision in script.walk_revisions():
        assert callable(revision.module.downgrade)
