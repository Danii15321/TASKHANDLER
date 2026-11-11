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
    "to_regclass('public.refresh_tokens') IS NOT NULL AS refresh_tokens_table, "
    "to_regclass('public.one_time_tokens') IS NOT NULL AS one_time_tokens_table, "
    "to_regclass('public.task_days') IS NOT NULL AS task_days_table, "
    "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'btree_gist') AS btree_gist, "
    "(SELECT count(*) FROM pg_policies WHERE schemaname = 'public') AS rls_policies, "
    "(SELECT count(*) FROM pg_class WHERE relrowsecurity "
    " AND relnamespace = 'public'::regnamespace) AS rls_tables"
)

# Droit explicite du rôle applicatif, `PUBLIC` exclu, privilèges par défaut (tables et séquences),
# version appliquée et présence des tables `users`, `refresh_tokens`, `one_time_tokens` et
# `task_days` (créée avec `tasks`), extension `btree_gist`, puis politiques RLS et tables où la
# RLS est active (`tasks` et `task_days`).
STATE_AT_HEAD = (1, False, 2, 1, True, True, True, True, True, 2, 2)
# Avant la RLS (révision 0006) : mêmes tables, aucune politique, RLS inactive.
STATE_BEFORE_RLS = (1, False, 2, 1, True, True, True, True, True, 0, 0)
# État initial de PostgreSQL : `PUBLIC` retrouve l'usage du schéma, aucune table ne subsiste.
STATE_AT_BASE = (0, True, 0, 0, False, False, False, False, False, 0, 0)

MigrationState = tuple[int, bool, int, int, bool, bool, bool, bool, bool, int, int]


# requires : `database_url` vise la base de test et le schéma contient `alembic_version`.
# assigns  : rien (lecture seule).
# ensures  : renvoie les droits accordés par les migrations et le nombre de versions appliquées.
def read_migration_state(database_url: str) -> MigrationState:
    # requires : aucune boucle d'événements n'est en cours dans ce fil d'exécution.
    # assigns  : ouvre puis ferme une connexion.
    # ensures  : renvoie l'état lu dans PostgreSQL.
    async def read() -> MigrationState:
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
            bool(state.one_time_tokens_table),
            bool(state.task_days_table),
            bool(state.btree_gist),
            int(state.rls_policies),
            int(state.rls_tables),
        )

    return asyncio.run(read())


# requires : `database_url` vise la base de test migrée ; `statement` est une requête fixe.
# assigns  : exécute la requête dans une transaction validée.
# ensures  : renvoie les valeurs de la première colonne du résultat, s'il y en a un.
def run_sql(database_url: str, statement: str) -> list[object]:
    # requires : aucune boucle d'événements n'est en cours dans ce fil d'exécution.
    # assigns  : ouvre puis ferme une connexion.
    # ensures  : renvoie les valeurs lues.
    async def run() -> list[object]:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(statement))
                return list(result.scalars()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(run())


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


# requires : la base de test est migrée.
# assigns  : annule puis réapplique la dernière migration (RLS) de la base de test ; crée puis
#            supprime un compte témoin.
# ensures  : annuler la RLS la désactive, retire ses politiques et convertit le motif de
#            révocation `password_change` sans toucher au reste ; la réappliquer les rétablit.
def test_row_level_security_migration_is_reversible(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> None:
    database_url = database_settings.migration_database_url.get_secret_value()
    config = alembic_config(database_url)

    # Une session révoquée par changement de mot de passe : ce motif n'existe pas avant 0007.
    run_sql(
        database_url,
        "WITH account AS (INSERT INTO users (email, password_hash) VALUES "
        "('migration@exemple.org', '$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA') RETURNING id) "
        "INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at, "
        "session_expires_at, revoked_at, revocation_reason) SELECT id, gen_random_uuid(), "
        "repeat('f', 64), now() + interval '1 day', now() + interval '1 day', now(), "
        "'password_change' FROM account",
    )
    try:
        command.downgrade(config, "-1")
        assert read_migration_state(database_url) == STATE_BEFORE_RLS
        # Le motif est ramené au plus proche motif connu, sinon la contrainte serait violée.
        assert run_sql(database_url, "SELECT revocation_reason FROM refresh_tokens") == [
            "logout_all"
        ]
    finally:
        command.upgrade(config, "head")
        # Suppression ciblée du seul compte créé ici ; ses sessions suivent en cascade.
        run_sql(database_url, "DELETE FROM users WHERE email = 'migration@exemple.org'")

    assert read_migration_state(database_url) == STATE_AT_HEAD


# requires : le dossier `migrations/` existe.
# assigns  : rien (lecture seule).
# ensures  : l'historique est linéaire et chaque révision possède une annulation.
def test_migration_history_is_linear_and_reversible() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert len(script.get_heads()) == 1
    for revision in script.walk_revisions():
        assert callable(revision.module.downgrade)
