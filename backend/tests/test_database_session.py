"""Tests sur un vrai PostgreSQL : délais imposés, session par requête et isolation des tests."""

from http import HTTPStatus

import httpx2
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import Column, Integer, MetaData, Table, UniqueConstraint, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.db.base import NAMING_CONVENTION, Base
from app.db.session import SessionDependency, create_database_engine
from tests.conftest import DatabaseTestSettings, isolated_session

pytestmark = pytest.mark.anyio

QUERY_CANCELED = "57014"
BOUND_VALUE = "valeur-liee-sensible"
INSERT_PROBE = text("INSERT INTO security_probe (id, label) VALUES (1, 'sonde')")
COUNT_PROBE = text("SELECT count(*) FROM security_probe")


# requires : la table de sonde existe.
# assigns  : rien.
# ensures  : renvoie le nombre de lignes validées, vu depuis une nouvelle connexion.
async def count_committed_rows(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        return int((await connection.execute(COUNT_PROBE)).scalar_one())


# requires : `database_settings` fournit l'URL du rôle applicatif.
# assigns  : rien (aucune connexion n'est ouverte ici).
# ensures  : renvoie le moteur de production, avec un délai de requête très court.
def engine_with_short_timeout(database_settings: DatabaseTestSettings) -> AsyncEngine:
    return create_database_engine(
        Settings(
            environment="test",
            database_url=database_settings.app_database_url,
            database_statement_timeout_ms=100,
            _env_file=None,
        )
    )


# requires : PostgreSQL est démarré.
# assigns  : ouvre puis ferme une connexion.
# ensures  : chaque connexion du moteur reçoit ses délais et le fuseau UTC dès son ouverture.
async def test_engine_applies_limits_to_each_connection(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> None:
    engine = engine_with_short_timeout(database_settings)
    try:
        async with engine.connect() as connection:
            statement_timeout = (await connection.execute(text("SHOW statement_timeout"))).scalar()
            lock_timeout = (await connection.execute(text("SHOW lock_timeout"))).scalar()
            idle_timeout = (
                await connection.execute(text("SHOW idle_in_transaction_session_timeout"))
            ).scalar()
            timezone = (await connection.execute(text("SHOW timezone"))).scalar()
    finally:
        await engine.dispose()

    assert statement_timeout == "100ms"
    assert lock_timeout == "2s"
    assert idle_timeout == "10s"
    assert timezone == "UTC"


# requires : PostgreSQL est démarré.
# assigns  : exécute une requête annulée par le serveur.
# ensures  : une requête trop longue est interrompue par PostgreSQL.
async def test_statement_timeout_cancels_slow_query(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> None:
    engine = engine_with_short_timeout(database_settings)
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError) as error:
                await connection.execute(text("SELECT pg_sleep(5)"))
    finally:
        await engine.dispose()

    assert getattr(error.value.orig, "sqlstate", None) == QUERY_CANCELED


# requires : PostgreSQL est démarré.
# assigns  : exécute une requête en échec.
# ensures  : une erreur SQL ne recopie jamais les valeurs liées, destinées aux journaux.
async def test_sql_errors_hide_bound_parameters(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> None:
    engine = engine_with_short_timeout(database_settings)
    statement = text("SELECT 1 / (CASE WHEN CAST(:value AS text) <> '' THEN 0 ELSE 1 END)")
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError) as error:
                await connection.execute(statement, {"value": BOUND_VALUE})
    finally:
        await engine.dispose()

    assert BOUND_VALUE not in str(error.value)


# requires : la table de sonde existe.
# assigns  : ajoute une route de test et tente une insertion, annulée.
# ensures  : une erreur imprévue dans une route annule ses écritures.
async def test_unexpected_error_rolls_back_writes(
    database_app: FastAPI,
    database_client: httpx2.AsyncClient,
    probe_table: str,
    app_engine: AsyncEngine,
) -> None:
    # requires : `session` est la session de la requête.
    # assigns  : insère une ligne, sans la valider.
    # ensures  : lève toujours une erreur après l'insertion.
    async def write_then_fail(session: SessionDependency) -> None:
        await session.execute(INSERT_PROBE)
        await session.flush()
        raise RuntimeError("panne après écriture")

    database_app.add_api_route("/write", write_then_fail, methods=["POST"])

    response = await database_client.post("/write")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert await count_committed_rows(app_engine) == 0


# requires : la table de sonde existe.
# assigns  : ajoute une route de test et tente une insertion, annulée.
# ensures  : une erreur HTTP volontaire annule aussi les écritures.
async def test_http_exception_rolls_back_writes(
    database_app: FastAPI,
    database_client: httpx2.AsyncClient,
    probe_table: str,
    app_engine: AsyncEngine,
) -> None:
    # requires : `session` est la session de la requête.
    # assigns  : insère une ligne, sans la valider.
    # ensures  : lève toujours une erreur 409 après l'insertion.
    async def write_then_conflict(session: SessionDependency) -> None:
        await session.execute(INSERT_PROBE)
        raise HTTPException(status_code=HTTPStatus.CONFLICT, detail="conflit")

    database_app.add_api_route("/write", write_then_conflict, methods=["POST"])

    response = await database_client.post("/write")

    assert response.status_code == HTTPStatus.CONFLICT
    assert await count_committed_rows(app_engine) == 0


# requires : la table de sonde existe.
# assigns  : ajoute une route de test et tente une insertion, jamais validée.
# ensures  : sans `commit` explicite, rien n'est écrit, même si la route réussit.
async def test_success_without_commit_writes_nothing(
    database_app: FastAPI,
    database_client: httpx2.AsyncClient,
    probe_table: str,
    app_engine: AsyncEngine,
) -> None:
    # requires : `session` est la session de la requête.
    # assigns  : insère une ligne, sans la valider.
    # ensures  : se termine normalement.
    async def write_without_commit(session: SessionDependency) -> None:
        await session.execute(INSERT_PROBE)

    database_app.add_api_route("/write", write_without_commit, methods=["POST"])

    response = await database_client.post("/write")

    assert response.status_code == HTTPStatus.OK
    assert await count_committed_rows(app_engine) == 0


# requires : la table de sonde existe.
# assigns  : ajoute une route de test et valide une insertion dans la table de sonde.
# ensures  : un `commit` explicite écrit réellement avec le rôle applicatif.
async def test_explicit_commit_persists_writes(
    database_app: FastAPI,
    database_client: httpx2.AsyncClient,
    probe_table: str,
    app_engine: AsyncEngine,
) -> None:
    # requires : `session` est la session de la requête.
    # assigns  : insère et valide une ligne.
    # ensures  : se termine normalement.
    async def write_and_commit(session: SessionDependency) -> None:
        await session.execute(INSERT_PROBE)
        await session.commit()

    database_app.add_api_route("/write", write_and_commit, methods=["POST"])

    response = await database_client.post("/write")

    assert response.status_code == HTTPStatus.OK
    assert await count_committed_rows(app_engine) == 1


# requires : la table de sonde existe.
# assigns  : insère une ligne dans une transaction annulée à la sortie.
# ensures  : même un `commit` dans une session isolée ne laisse aucune trace après le test.
async def test_isolated_session_discards_even_commits(
    probe_table: str, app_engine: AsyncEngine
) -> None:
    async with isolated_session(app_engine) as session:
        await session.execute(INSERT_PROBE)
        await session.commit()
        assert (await session.execute(COUNT_PROBE)).scalar_one() == 1

    assert await count_committed_rows(app_engine) == 0


# requires : `db_session` fournit une session isolée.
# assigns  : rien.
# ensures  : la fixture relie bien les tests au rôle applicatif, sur la base dédiée.
async def test_db_session_uses_application_role(db_session: AsyncSession) -> None:
    identity = (await db_session.execute(text("SELECT current_user, current_database()"))).one()

    assert tuple(identity) == ("taskhandler_app", "taskhandler_test")


# requires : rien.
# assigns  : rien.
# ensures  : la base déclarative nomme les contraintes de façon prévisible pour les migrations.
def test_constraint_names_are_predictable() -> None:
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    table = Table(
        "sample",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("code", Integer),
        UniqueConstraint("code"),
    )

    names = {constraint.name for constraint in table.constraints}

    assert names == {"pk_sample", "uq_sample_code"}
    assert Base.metadata.naming_convention == NAMING_CONVENTION
