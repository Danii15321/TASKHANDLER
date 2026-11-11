"""Fixtures partagées par les tests."""

import io
import json
import logging
import os
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Self

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import APP_DATABASE_ROLE, Settings
from app.core.logging_config import JsonFormatter
from app.db.session import get_session
from app.main import create_app

LogReader = Callable[[], list[dict[str, object]]]

BACKEND_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_NAME = "taskhandler_test"
PROBE_TABLE = "security_probe"

# Clé de signature tirée à chaque exécution : aucun test ne dépend d'une clé connue à l'avance.
TEST_JWT_SECRET = secrets.token_hex(32)

# Valeur longue et sans mot d'exemple, acceptée par la validation mais reconnue par aucun serveur.
UNUSED_CREDENTIAL = "k" * 40


# requires : `query` est vide ou commence par `?`.
# assigns  : rien.
# ensures  : renvoie une URL valide vers un port fermé : aucune connexion ne peut aboutir.
def unreachable_database_url(query: str = "") -> SecretStr:
    return SecretStr(
        f"postgresql+asyncpg://{APP_DATABASE_ROLE}:{UNUSED_CREDENTIAL}@127.0.0.1:1/taskhandler{query}"
    )


# requires : rien.
# assigns  : rien.
# ensures  : renvoie une URL Redis valide vers un port fermé : aucune connexion ne peut aboutir.
#            `rediss://` la rend acceptable dans tous les environnements, production comprise.
def unreachable_redis_url() -> SecretStr:
    return SecretStr(f"rediss://taskhandler_app:{UNUSED_CREDENTIAL}@127.0.0.1:1/0")


# requires : rien.
# assigns  : rien.
# ensures  : renvoie un préfixe de clés propre à un test : les compteurs de deux tests ne se
#            mélangent jamais, sans avoir à vider Redis (ce que ses droits interdisent d'ailleurs).
def unique_rate_limit_prefix() -> str:
    return f"rl:test:{uuid.uuid4().hex}:"


class DatabaseTestSettings(BaseSettings):
    """URL de la base de test ; `.env.test` ou l'environnement doivent les fournir."""

    model_config = SettingsConfigDict(
        env_prefix="TEST_",
        env_file=BACKEND_ROOT / ".env.test",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    app_database_url: SecretStr
    migration_database_url: SecretStr
    redis_url: SecretStr

    # requires : les deux URL sont renseignées.
    # assigns  : rien.
    # ensures  : renvoie la configuration si les deux URL visent la base de test ; sinon lève
    #            `ValueError`. Les tests annulent des migrations : aucune autre base n'est permise.
    @model_validator(mode="after")
    def _require_dedicated_database(self) -> Self:
        for url in (self.app_database_url, self.migration_database_url):
            if make_url(url.get_secret_value()).database != TEST_DATABASE_NAME:
                raise ValueError(f"les tests doivent viser la base {TEST_DATABASE_NAME}")
        return self


# requires : `database_url` vise la base de test, avec le rôle de migration.
# assigns  : rien.
# ensures  : renvoie une configuration Alembic ciblant cette base, sans lire `.env.migration`.
def alembic_config(database_url: str) -> Config:
    config = Config(BACKEND_ROOT / "alembic.ini")
    config.attributes["database_url"] = database_url
    return config


# requires : `engine` est connecté à la base de test.
# assigns  : ouvre une transaction, toujours annulée à la sortie.
# ensures  : fournit une session dont même les `commit` sont annulés à la fin : ils ne valident
#            qu'un point de sauvegarde, à l'intérieur de la transaction extérieure.
@asynccontextmanager
async def isolated_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


# requires : rien.
# assigns  : retire du processus les variables `TASKHANDLER_*` et fixe la clé JWT de test, le
#            temps du test.
# ensures  : un test ne dépend jamais de la configuration de la machine qui l'exécute ; seule la
#            clé de signature, obligatoire, est fournie (un test peut la retirer).
@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("TASKHANDLER_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("TASKHANDLER_JWT_SECRET", TEST_JWT_SECRET)
    monkeypatch.setenv("TASKHANDLER_REDIS_URL", unreachable_redis_url().get_secret_value())


# requires : rien.
# assigns  : rien.
# ensures  : les tests asynchrones s'exécutent sur asyncio, la boucle utilisée en production.
@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# requires : rien.
# assigns  : la configuration globale des journaux, via `create_app`.
# ensures  : renvoie une application dont la base est injoignable, sans lire de fichier `.env`.
@pytest.fixture
def app() -> FastAPI:
    return create_app(
        Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)
    )


# requires : `app` est une application assemblée.
# assigns  : rien.
# ensures  : renvoie un client qui reçoit les réponses 500 au lieu de relancer les exceptions.
@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# requires : `app` est assemblée, car `create_app` réinitialise les gestionnaires de journaux.
# assigns  : ajoute un gestionnaire au journal racine le temps du test.
# ensures  : fournit une fonction qui renvoie les événements écrits par le vrai formateur.
@pytest.fixture
def read_logs(app: FastAPI) -> Iterator[LogReader]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # requires : rien.
    # assigns  : rien.
    # ensures  : renvoie les événements déjà écrits, décodés depuis leur ligne JSON.
    def read() -> list[dict[str, object]]:
        return [json.loads(line) for line in stream.getvalue().splitlines()]

    yield read
    root_logger.removeHandler(handler)


# requires : `.env.test` ou les variables `TEST_*` sont renseignés ; sinon le test échoue.
# assigns  : rien.
# ensures  : renvoie les URL validées de la base de test.
@pytest.fixture(scope="session")
def database_settings() -> DatabaseTestSettings:
    return DatabaseTestSettings()


# requires : PostgreSQL est démarré et la base de test existe.
# assigns  : applique toutes les migrations à la base de test.
# ensures  : le schéma de la base de test est à la dernière révision.
@pytest.fixture(scope="session")
def migrated_database(database_settings: DatabaseTestSettings) -> None:
    command.upgrade(
        alembic_config(database_settings.migration_database_url.get_secret_value()), "head"
    )


# requires : la base de test est migrée.
# assigns  : ouvre puis ferme des connexions avec le rôle applicatif.
# ensures  : fournit un moteur sans pool, qui ne laisse aucune connexion après le test.
@pytest.fixture
async def app_engine(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        database_settings.app_database_url.get_secret_value(), poolclass=NullPool
    )
    yield engine
    await engine.dispose()


# requires : la base de test est migrée.
# assigns  : ouvre puis ferme des connexions avec le rôle de migration.
# ensures  : fournit un moteur sans pool, qui ne laisse aucune connexion après le test.
@pytest.fixture
async def migrator_engine(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        database_settings.migration_database_url.get_secret_value(), poolclass=NullPool
    )
    yield engine
    await engine.dispose()


# requires : `app_engine` est connecté à la base de test.
# assigns  : ouvre une transaction, toujours annulée à la fin du test.
# ensures  : fournit une session isolée : un test ne voit jamais les écritures d'un autre.
@pytest.fixture
async def db_session(app_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with isolated_session(app_engine) as session:
        yield session


# requires : `migrator_engine` est connecté à la base de test.
# assigns  : crée la table de sonde dans la base de test et la supprime à la fin.
# ensures  : fournit le nom d'une table réellement validée, créée par le rôle de migration.
@pytest.fixture
async def probe_table(migrator_engine: AsyncEngine) -> AsyncIterator[str]:
    async with migrator_engine.begin() as connection:
        # Une exécution interrompue a pu laisser cette table de test derrière elle.
        await connection.execute(text("DROP TABLE IF EXISTS security_probe"))
        await connection.execute(
            text("CREATE TABLE security_probe (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
        )
    yield PROBE_TABLE
    async with migrator_engine.begin() as connection:
        await connection.execute(text("DROP TABLE security_probe"))


# requires : la base de test est migrée.
# assigns  : la configuration globale des journaux ; ferme le moteur à la fin du test.
# ensures  : fournit une application réellement reliée à PostgreSQL et à Redis, avec des
#            compteurs de limitation propres au test.
@pytest.fixture
async def database_app(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> AsyncIterator[FastAPI]:
    application = create_app(
        Settings(
            environment="test",
            database_url=database_settings.app_database_url,
            redis_url=database_settings.redis_url,
            rate_limit_key_prefix=unique_rate_limit_prefix(),
            _env_file=None,
        )
    )
    yield application
    await application.state.engine.dispose()
    await application.state.redis.aclose()


# requires : `database_app` est reliée à PostgreSQL.
# assigns  : rien.
# ensures  : fournit un client asynchrone, sur la même boucle d'événements que le test.
@pytest.fixture
async def database_client(database_app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=database_app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


# requires : `database_app` est reliée à PostgreSQL, `db_session` est une session isolée.
# assigns  : remplace la session des routes par `db_session` le temps du test.
# ensures  : fournit un client dont toutes les écritures, même validées, sont annulées à la fin.
@pytest.fixture
async def isolated_client(
    database_app: FastAPI, db_session: AsyncSession
) -> AsyncIterator[httpx2.AsyncClient]:
    # requires : rien.
    # assigns  : rien.
    # ensures  : fournit la session isolée du test à la place d'une nouvelle session.
    async def use_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    database_app.dependency_overrides[get_session] = use_test_session
    transport = httpx2.ASGITransport(app=database_app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client
    database_app.dependency_overrides.clear()


REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
LOGOUT_ALL_URL = "/api/v1/auth/logout-all"
TEST_PASSPHRASE = "violet ruisseau lanterne 2026"


# requires : `client` est relié à l'application de test.
# assigns  : crée un compte.
# ensures  : renvoie l'identifiant du compte créé ; échoue si l'inscription est refusée.
async def create_account(
    client: httpx2.AsyncClient, email: str = "alice@exemple.org", password: str = TEST_PASSPHRASE
) -> str:
    response = await client.post(REGISTER_URL, json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    account_id: str = response.json()["id"]
    return account_id


# requires : `client` est relié à l'application de test, le compte existe.
# assigns  : ouvre une session.
# ensures  : renvoie la paire de jetons ; échoue si la connexion est refusée.
async def login_tokens(
    client: httpx2.AsyncClient, email: str = "alice@exemple.org", password: str = TEST_PASSPHRASE
) -> dict[str, str]:
    response = await client.post(LOGIN_URL, json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    tokens: dict[str, str] = response.json()
    return tokens


# requires : rien.
# assigns  : rien.
# ensures  : renvoie l'en-tête d'authentification d'un jeton d'accès.
def bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}
