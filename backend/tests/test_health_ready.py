"""Tests de la sonde de disponibilité, avec une base réelle puis en panne."""

import asyncio
from http import HTTPStatus

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import INTERNAL_ERROR_DETAIL
from app.main import create_app
from app.modules.health import router as health_router
from tests.conftest import UNUSED_CREDENTIAL, DatabaseTestSettings, LogReader

PROBLEM_MEDIA_TYPE = "application/problem+json"
INFRASTRUCTURE_DETAILS = ("127.0.0.1", "taskhandler", "postgres", "asyncpg", UNUSED_CREDENTIAL)


# requires : PostgreSQL est démarré.
# assigns  : rien.
# ensures  : avec une base joignable, la sonde répond sans aucun détail.
@pytest.mark.anyio
async def test_ready_returns_ok_when_database_answers(
    database_client: httpx2.AsyncClient,
) -> None:
    response = await database_client.get("/health/ready")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}


# requires : `client` est relié à une base injoignable.
# assigns  : rien.
# ensures  : la panne donne un 503 générique, journalisé sans détail de connexion.
def test_ready_hides_infrastructure_when_database_is_down(
    client: TestClient, read_logs: LogReader
) -> None:
    response = client.get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == INTERNAL_ERROR_DETAIL
    for detail in INFRASTRUCTURE_DETAILS:
        assert detail not in response.text

    events = [event for event in read_logs() if event["message"] == "database_unavailable"]
    assert len(events) == 1
    assert UNUSED_CREDENTIAL not in str(read_logs())


# requires : `client` est relié à une application assemblée.
# assigns  : remplace le temps du test la requête de sonde par une attente plus longue.
# ensures  : une base qui ne répond plus ne retient pas la requête au-delà du délai imparti.
def test_ready_times_out_when_database_hangs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : attend bien plus longtemps que le délai de la sonde.
    async def hang(_session: AsyncSession) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(health_router, "ping_database", hang)
    monkeypatch.setattr(health_router, "_READINESS_TIMEOUT_SECONDS", 0.05)

    assert client.get("/health/ready").status_code == HTTPStatus.SERVICE_UNAVAILABLE


# requires : PostgreSQL est démarré.
# assigns  : provoque un échec d'authentification sur la base de test.
# ensures  : un mot de passe refusé donne le même 503 générique qu'une base injoignable.
@pytest.mark.anyio
async def test_ready_hides_rejected_credentials(
    migrated_database: None, database_settings: DatabaseTestSettings
) -> None:
    real_url = make_url(database_settings.app_database_url.get_secret_value())
    wrong_url = real_url.set(password=UNUSED_CREDENTIAL)
    application = create_app(
        Settings(
            environment="test",
            database_url=SecretStr(wrong_url.render_as_string(hide_password=False)),
            _env_file=None,
        )
    )
    transport = httpx2.ASGITransport(app=application, raise_app_exceptions=False)

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as async_client:
        response = await async_client.get("/health/ready")
    await application.state.engine.dispose()

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["detail"] == INTERNAL_ERROR_DETAIL
