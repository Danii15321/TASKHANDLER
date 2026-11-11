"""Moteur asynchrone, fabrique de sessions et session propre à chaque requête."""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

# Une connexion n'est pas conservée indéfiniment : un serveur redémarré ou un pare-feu qui
# coupe les connexions anciennes ne doit pas laisser de connexions mortes dans le pool.
_POOL_RECYCLE_SECONDS = 1800

# Marge côté client : si le réseau se fige, le serveur ne peut plus annuler lui-même la requête.
_COMMAND_TIMEOUT_MARGIN_SECONDS = 1.0


# requires : `settings` est valide.
# assigns  : rien (aucune connexion n'est ouverte avant la première requête).
# ensures  : renvoie un moteur au pool borné, dont chaque connexion impose ses délais.
def create_database_engine(settings: Settings) -> AsyncEngine:
    statement_timeout_ms = settings.database_statement_timeout_ms
    return create_async_engine(
        settings.database_url.get_secret_value(),
        # Les valeurs liées peuvent être des données personnelles : elles ne doivent apparaître
        # ni dans les messages d'erreur de SQLAlchemy, ni donc dans les journaux.
        hide_parameters=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=_POOL_RECYCLE_SECONDS,
        # Teste la connexion avant de la confier à une requête.
        pool_pre_ping=True,
        connect_args={
            "timeout": settings.database_connect_timeout_seconds,
            "command_timeout": statement_timeout_ms / 1000 + _COMMAND_TIMEOUT_MARGIN_SECONDS,
            # Ces réglages s'appliquent dès l'ouverture de la connexion : aucune requête ne
            # peut s'exécuter sans eux, et la limite est appliquée par PostgreSQL lui-même.
            "server_settings": {
                "application_name": "taskhandler-api",
                "timezone": "UTC",
                "statement_timeout": str(statement_timeout_ms),
                "lock_timeout": str(settings.database_lock_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    settings.database_idle_transaction_timeout_ms
                ),
            },
        },
    )


# requires : `engine` est un moteur valide.
# assigns  : rien.
# ensures  : renvoie une fabrique de sessions asynchrones liées à ce moteur.
def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # `expire_on_commit=False` : après un commit, lire un attribut ne doit pas relancer une
    # requête implicite, interdite en asynchrone hors d'un contexte prévu à cet effet.
    return async_sessionmaker(engine, expire_on_commit=False)


# requires : l'application a été assemblée par `create_app`.
# assigns  : ouvre une transaction, annulée si elle n'a pas été validée explicitement.
# ensures  : fournit une session propre à la requête, toujours fermée à la fin ; toute exception
#            levée par la route annule la transaction, puis est relancée.
async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            # Aucune écriture partielle ne doit survivre à une erreur. Le commit reste explicite,
            # dans la couche service : une route ne valide jamais une transaction par accident.
            await session.rollback()
            raise


SessionDependency = Annotated[AsyncSession, Depends(get_session)]


# Fabrique de sessions pour les tâches d'arrière-plan : elles s'exécutent après la réponse, quand
# la session de la requête est déjà fermée, et doivent donc ouvrir la leur.
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


# requires : l'application a été assemblée par `create_app`.
# assigns  : rien.
# ensures  : renvoie la fabrique de sessions de l'application ; les tests la remplacent pour que
#            les tâches d'arrière-plan écrivent dans leur transaction annulée.
def get_session_factory(request: Request) -> SessionFactory:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return session_factory


SessionFactoryDependency = Annotated[SessionFactory, Depends(get_session_factory)]
