"""Point d'entrée d'Alembic : exécute les migrations avec le rôle dédié au schéma."""

import asyncio

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import MigrationSettings
from app.core.logging_config import configure_logging
from app.db.base import Base

config = context.config

# Clé réservée aux tests, qui ciblent exclusivement la base `taskhandler_test`.
_INJECTED_URL_KEY = "database_url"

# Une migration attend au plus 5 secondes un verrou : sinon, elle bloquerait en file d'attente
# toutes les requêtes de l'application qui touchent la même table.
_LOCK_TIMEOUT_MS = "5000"
_CONNECT_TIMEOUT_SECONDS = 10


# requires : rien.
# assigns  : rien.
# ensures  : renvoie l'URL injectée par un test, sinon celle de `MigrationSettings` ; lève
#            `ValidationError` si la configuration du rôle de migration est invalide.
def _resolve_database_url() -> str:
    injected_url = config.attributes.get(_INJECTED_URL_KEY)
    if isinstance(injected_url, str):
        return injected_url
    return MigrationSettings().database_url.get_secret_value()


# requires : `connection` est une connexion ouverte avec le rôle de migration.
# assigns  : le schéma PostgreSQL et la table `alembic_version`.
# ensures  : les migrations demandées sont appliquées dans une transaction.
def _apply_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# requires : `database_url` désigne le rôle de migration.
# assigns  : le schéma PostgreSQL, via `_apply_migrations`.
# ensures  : les migrations sont validées ensemble, et la connexion est toujours fermée.
async def _run_online_migrations(database_url: str) -> None:
    engine = create_async_engine(
        database_url,
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={
            "timeout": _CONNECT_TIMEOUT_SECONDS,
            "server_settings": {
                "application_name": "taskhandler-migrations",
                "timezone": "UTC",
                "lock_timeout": _LOCK_TIMEOUT_MS,
            },
        },
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_apply_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    # Le mode hors ligne produirait un script SQL sans passer par le rôle de migration ni par
    # les protections de ce fichier : il n'a pas d'usage dans ce projet.
    raise RuntimeError("Le mode hors ligne d'Alembic n'est pas pris en charge.")

# Sans URL injectée, Alembic est lancé en ligne de commande : ses journaux passent alors par le
# formateur filtré. Dans les tests, la configuration des journaux reste celle du test.
if _INJECTED_URL_KEY not in config.attributes:
    configure_logging("INFO")

asyncio.run(_run_online_migrations(_resolve_database_url()))
