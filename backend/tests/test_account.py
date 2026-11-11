"""Tests du compte : lecture, changement de mot de passe, suppression et export RGPD."""

import secrets
from http import HTTPStatus
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.rate_limit import RateLimiter, create_redis_client, get_rate_limiter
from tests.conftest import (
    LOGIN_URL,
    REFRESH_URL,
    TEST_JWT_SECRET,
    TEST_PASSPHRASE,
    LogReader,
    OutboxClient,
    bearer,
    create_account,
    login_tokens,
    unreachable_database_url,
    unreachable_redis_url,
)

pytestmark = pytest.mark.anyio

ME_URL = "/api/v1/users/me"
EMAIL = "alice@exemple.org"
NEW_PASSPHRASE = "orage sur la colline 1987"
TASK = {
    "title": "Tâche exportée",
    "description": "Description privée d'Alice",
    "days": [{"day": "2026-11-16", "start_hour": 9, "duration_hours": 1}],
}


# requires : `isolated_client` est relié à l'application de test.
# assigns  : crée Alice et ouvre une session.
# ensures  : fournit la paire de jetons d'Alice.
@pytest.fixture
async def alice(isolated_client: OutboxClient) -> dict[str, str]:
    await create_account(isolated_client, email=EMAIL)
    return await login_tokens(isolated_client, email=EMAIL)


# requires : `client` est relié à l'application.
# assigns  : envoie une connexion.
# ensures  : renvoie son code HTTP.
async def login_status(client: OutboxClient, password: str) -> int:
    response = await client.post(LOGIN_URL, json={"email": EMAIL, "password": password})
    return response.status_code


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte, dans une transaction annulée à la fin du test.
# ensures  : `GET /users/me` renvoie la vue publique, sans empreinte ni champ interne.
async def test_read_me(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    response = await isolated_client.get(ME_URL, headers=bearer(alice["access_token"]))

    assert response.status_code == HTTPStatus.OK
    assert set(response.json()) == {"id", "email", "email_verified_at", "created_at"}
    assert response.json()["email"] == EMAIL
    for marker in ("password", "argon2", "token_version", "is_active"):
        assert marker not in response.text


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : change le mot de passe, dans une transaction annulée à la fin du test.
# ensures  : le nouveau mot de passe fonctionne, l'ancien non, et toutes les sessions tombent,
#            y compris celle qui a fait la demande ; l'événement est journalisé sans secret.
async def test_change_password(
    isolated_client: OutboxClient, alice: dict[str, str], read_logs: LogReader
) -> None:
    other_device = await login_tokens(isolated_client, email=EMAIL)

    response = await isolated_client.post(
        f"{ME_URL}/password",
        json={"current_password": TEST_PASSPHRASE, "new_password": NEW_PASSPHRASE},
        headers=bearer(alice["access_token"]),
    )

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert await login_status(isolated_client, TEST_PASSPHRASE) == HTTPStatus.UNAUTHORIZED
    assert await login_status(isolated_client, NEW_PASSPHRASE) == HTTPStatus.OK
    for tokens in (alice, other_device):
        me = await isolated_client.get(ME_URL, headers=bearer(tokens["access_token"]))
        assert me.status_code == HTTPStatus.UNAUTHORIZED
        refreshed = await isolated_client.post(
            REFRESH_URL, json={"refresh_token": tokens["refresh_token"]}
        )
        assert refreshed.status_code == HTTPStatus.UNAUTHORIZED
    events = [event for event in read_logs() if event["message"] == "password_changed"]
    assert len(events) == 1
    logs = str(read_logs())
    for secret in (TEST_PASSPHRASE, NEW_PASSPHRASE, EMAIL):
        assert secret not in logs


# requires : `body` est une demande de changement refusée.
# assigns  : rien (la demande est refusée sans rien modifier).
# ensures  : mot de passe actuel faux (403), nouveau trop faible ou reprenant l'adresse (422),
#            champ inconnu (422) : l'ancien mot de passe reste valable.
@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"current_password": "mauvais mot de passe 99", "new_password": NEW_PASSPHRASE}, 403),
        ({"current_password": TEST_PASSPHRASE, "new_password": "asdfasdfasdf"}, 422),
        ({"current_password": TEST_PASSPHRASE, "new_password": "alice-violet-2026"}, 422),
        (
            {"current_password": TEST_PASSPHRASE, "new_password": NEW_PASSPHRASE, "user_id": "x"},
            422,
        ),
        ({"new_password": NEW_PASSPHRASE}, 422),
    ],
)
async def test_refused_password_change_changes_nothing(
    isolated_client: OutboxClient, alice: dict[str, str], body: dict[str, Any], status: int
) -> None:
    response = await isolated_client.post(
        f"{ME_URL}/password", json=body, headers=bearer(alice["access_token"])
    )

    assert response.status_code == status
    assert await login_status(isolated_client, TEST_PASSPHRASE) == HTTPStatus.OK


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : enchaîne des réauthentifications fausses, dans une transaction annulée à la fin.
# ensures  : un jeton volé ne permet pas de deviner le mot de passe à la chaîne :
#            429 après 5 essais.
async def test_reauthentication_is_rate_limited(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    statuses = []
    for _ in range(6):
        response = await isolated_client.post(
            f"{ME_URL}/password",
            json={"current_password": "mauvais mot de passe 99", "new_password": NEW_PASSPHRASE},
            headers=bearer(alice["access_token"]),
        )
        statuses.append(response.status_code)

    assert statuses == [HTTPStatus.FORBIDDEN] * 5 + [HTTPStatus.TOO_MANY_REQUESTS]


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée puis supprime un compte et ses données, dans une transaction annulée à la fin.
# ensures  : un mot de passe faux ne supprime rien ; le bon supprime le compte, ses tâches et ses
#            sessions, sans laisser l'adresse dans les journaux.
async def test_delete_account(
    isolated_client: OutboxClient,
    alice: dict[str, str],
    db_session: AsyncSession,
    read_logs: LogReader,
) -> None:
    headers = bearer(alice["access_token"])
    await isolated_client.post("/api/v1/tasks", json=TASK, headers=headers)

    refused = await isolated_client.request(
        "DELETE", ME_URL, json={"password": "mauvais mot de passe 99"}, headers=headers
    )
    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert await login_status(isolated_client, TEST_PASSPHRASE) == HTTPStatus.OK

    deleted = await isolated_client.request(
        "DELETE", ME_URL, json={"password": TEST_PASSPHRASE}, headers=headers
    )

    assert deleted.status_code == HTTPStatus.NO_CONTENT
    assert await login_status(isolated_client, TEST_PASSPHRASE) == HTTPStatus.UNAUTHORIZED
    remaining = (
        await db_session.execute(
            text(
                "SELECT (SELECT count(*) FROM users) + (SELECT count(*) FROM refresh_tokens) "
                "+ (SELECT count(*) FROM one_time_tokens)"
            )
        )
    ).scalar_one()
    assert remaining == 0
    assert any(event["message"] == "account_deleted" for event in read_logs())
    assert EMAIL not in str(read_logs())


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée des tâches pour Alice et Bob, dans une transaction annulée à la fin du test.
# ensures  : l'export contient toutes les données d'Alice, description comprise, en pièce jointe
#            jamais mise en cache, et rien de Bob ni aucune empreinte.
async def test_export_contains_only_own_data(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    headers = bearer(alice["access_token"])
    await isolated_client.post("/api/v1/tasks", json=TASK, headers=headers)
    await create_account(isolated_client, email="bob@exemple.org")
    bob = await login_tokens(isolated_client, email="bob@exemple.org")
    await isolated_client.post(
        "/api/v1/tasks",
        json={**TASK, "title": "Secret de Bob", "description": "Description de Bob"},
        headers=bearer(bob["access_token"]),
    )

    response = await isolated_client.get(f"{ME_URL}/export", headers=headers)

    assert response.status_code == HTTPStatus.OK
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    export = response.json()
    assert set(export) == {"exported_at", "account", "tasks", "sessions"}
    assert export["account"]["email"] == EMAIL
    assert [task["description"] for task in export["tasks"]] == ["Description privée d'Alice"]
    assert export["tasks"][0]["days"][0]["day"] == "2026-11-16"
    assert len(export["sessions"]) == 1
    for forbidden in ("Bob", "token_hash", "password", "argon2", alice["refresh_token"]):
        assert forbidden not in response.text


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : enchaîne des exports, dans une transaction annulée à la fin du test.
# ensures  : l'export, coûteux, est limité à 5 par heure.
async def test_export_is_rate_limited(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    statuses = [
        (
            await isolated_client.get(f"{ME_URL}/export", headers=bearer(alice["access_token"]))
        ).status_code
        for _ in range(6)
    ]

    assert statuses == [HTTPStatus.OK] * 5 + [HTTPStatus.TOO_MANY_REQUESTS]


# requires : `method`, `path` et `body` décrivent une route du compte.
# assigns  : rien (la demande est refusée).
# ensures  : chaque route du compte exige un jeton valide : sans jeton, expiré ou falsifié, 401.
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", ME_URL, None),
        ("POST", f"{ME_URL}/password", {"current_password": "x", "new_password": NEW_PASSPHRASE}),
        ("DELETE", ME_URL, {"password": TEST_PASSPHRASE}),
        ("GET", f"{ME_URL}/export", None),
    ],
)
async def test_account_routes_require_valid_token(
    isolated_client: OutboxClient,
    alice: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    claims = jwt.decode(alice["access_token"], options={"verify_signature": False})
    expired = {**claims, "iat": claims["iat"] - 3600, "exp": claims["exp"] - 3600}

    for headers in [
        {},
        bearer(jwt.encode(expired, TEST_JWT_SECRET, algorithm="HS256")),
        bearer(jwt.encode(claims, secrets.token_hex(32), algorithm="HS256")),
    ]:
        response = await isolated_client.request(method, path, json=body, headers=headers)
        assert response.status_code == HTTPStatus.UNAUTHORIZED

    assert await login_status(isolated_client, TEST_PASSPHRASE) == HTTPStatus.OK


# requires : PostgreSQL et Redis sont démarrés ; `method`, `path` et `body` décrivent une route du
#            compte protégée par une limite propre.
# assigns  : prive l'application de Redis après la connexion d'Alice, le temps du test.
# ensures  : sans Redis, ces routes refusent (503) au lieu de laisser deviner le mot de passe sans
#            limite, et le compte reste intact.
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            f"{ME_URL}/password",
            {"current_password": TEST_PASSPHRASE, "new_password": NEW_PASSPHRASE},
        ),
        ("DELETE", ME_URL, {"password": TEST_PASSPHRASE}),
        ("GET", f"{ME_URL}/export", None),
    ],
)
async def test_account_protection_fails_closed_without_redis(
    database_app: FastAPI,
    isolated_client: OutboxClient,
    alice: dict[str, str],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    redis = create_redis_client(
        Settings(
            environment="test",
            database_url=unreachable_database_url(),
            redis_url=unreachable_redis_url(),
            _env_file=None,
        )
    )
    database_app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(
        redis, key_prefix="rl:"
    )
    try:
        response = await isolated_client.request(
            method, path, json=body, headers=bearer(alice["access_token"])
        )
    finally:
        del database_app.dependency_overrides[get_rate_limiter]
        await redis.aclose()

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert await login_status(isolated_client, TEST_PASSPHRASE) == HTTPStatus.OK
