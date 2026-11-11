"""Tests des sessions : `get_current_user`, renouvellement, réutilisation et déconnexion."""

import asyncio
import base64
import json
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import httpx2
import jwt
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.tokens import ACCESS_TOKEN_TTL, create_access_token
from app.modules.auth.dependencies import CurrentUserDependency
from tests.conftest import (
    LOGOUT_ALL_URL,
    LOGOUT_URL,
    REFRESH_URL,
    TEST_JWT_SECRET,
    LogReader,
    bearer,
    create_account,
    login_tokens,
)

pytestmark = pytest.mark.anyio

PROTECTED_URL = "/protected"
PROBLEM_MEDIA_TYPE = "application/problem+json"


# requires : `database_app` est l'application de test.
# assigns  : ajoute une route de test protégée par `get_current_user`.
# ensures  : renvoie l'application, dont la route `/protected` renvoie l'identifiant du compte.
@pytest.fixture
def protected_app(database_app: FastAPI) -> FastAPI:
    # requires : un jeton d'accès valide.
    # assigns  : rien.
    # ensures  : renvoie l'identifiant du compte authentifié.
    async def whoami(current: CurrentUserDependency) -> dict[str, str]:
        return {"user_id": str(current.user.id)}

    database_app.add_api_route(PROTECTED_URL, whoami)
    return database_app


# requires : `client` est relié à l'application de test.
# assigns  : rien.
# ensures  : renvoie le code HTTP de la route protégée pour ce jeton d'accès.
async def protected_status(client: httpx2.AsyncClient, access_token: str) -> int:
    return (await client.get(PROTECTED_URL, headers=bearer(access_token))).status_code


# requires : `client` est relié à l'application de test.
# assigns  : envoie une demande de renouvellement.
# ensures  : renvoie la réponse HTTP brute.
async def refresh(client: httpx2.AsyncClient, refresh_token: str) -> httpx2.Response:
    return await client.post(REFRESH_URL, json={"refresh_token": refresh_token})


# requires : rien.
# assigns  : rien.
# ensures  : renvoie un segment JWT encodé en base64url.
def segment(data: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


# requires : `access_token` est un jeton valide émis par l'application.
# assigns  : rien.
# ensures  : renvoie ses revendications, lues sans vérification (pour en fabriquer des variantes).
def claims_of(access_token: str) -> dict[str, Any]:
    claims: dict[str, Any] = jwt.decode(access_token, options={"verify_signature": False})
    return claims


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : un jeton d'accès valide donne accès à une route protégée, pour le bon compte.
async def test_valid_access_token_grants_access(
    protected_app: FastAPI, isolated_client: httpx2.AsyncClient
) -> None:
    account_id = await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)

    response = await isolated_client.get(PROTECTED_URL, headers=bearer(tokens["access_token"]))

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"user_id": account_id}


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : en-tête absent, autre schéma, jeton illisible, expiré, falsifié, non signé ou d'un
#            autre algorithme : toujours 401, au même format, avec `WWW-Authenticate`.
async def test_invalid_credentials_are_rejected(
    protected_app: FastAPI, isolated_client: httpx2.AsyncClient
) -> None:
    await create_account(isolated_client)
    valid = claims_of((await login_tokens(isolated_client))["access_token"])
    expired = {**valid, "iat": valid["iat"] - 3600, "exp": valid["exp"] - 3600}
    other_key = secrets.token_hex(32)
    headers_cases: list[dict[str, str]] = [
        {},
        {"Authorization": "Basic YWxpY2U6c2VjcmV0"},
        bearer("pas-un-jeton"),
        bearer(jwt.encode(expired, TEST_JWT_SECRET, algorithm="HS256")),
        bearer(jwt.encode(valid, other_key, algorithm="HS256")),
        bearer(jwt.encode(valid, TEST_JWT_SECRET, algorithm="HS512")),
        bearer(f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(valid)}."),
    ]

    for headers in headers_cases:
        response = await isolated_client.get(PROTECTED_URL, headers=headers)
        assert response.status_code == HTTPStatus.UNAUTHORIZED, headers
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
        assert response.json()["detail"] == "Authentification requise."


# requires : PostgreSQL est démarré et migré.
# assigns  : désactive le compte dans une transaction annulée à la fin du test.
# ensures  : un jeton encore valide est refusé dès que le compte est désactivé.
async def test_inactive_account_is_rejected(
    protected_app: FastAPI, isolated_client: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)
    await db_session.execute(text("UPDATE users SET is_active = false"))

    assert (
        await protected_status(isolated_client, tokens["access_token"]) == HTTPStatus.UNAUTHORIZED
    )


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : un jeton correctement signé mais pour un compte inconnu ou une session inexistante
#            est refusé : la signature ne suffit pas, la base fait foi.
async def test_signed_token_for_unknown_session_is_rejected(
    protected_app: FastAPI, isolated_client: httpx2.AsyncClient
) -> None:
    account_id = uuid.UUID(await create_account(isolated_client))
    await login_tokens(isolated_client)

    for user_id, session_id in [(uuid.uuid4(), uuid.uuid4()), (account_id, uuid.uuid4())]:
        token = create_access_token(
            secret=SecretStr(TEST_JWT_SECRET),
            user_id=user_id,
            session_id=session_id,
            token_version=0,
            now=datetime.now(UTC),
        )
        assert await protected_status(isolated_client, token) == HTTPStatus.UNAUTHORIZED


# requires : PostgreSQL est démarré et migré.
# assigns  : renouvelle une session dans une transaction annulée à la fin du test.
# ensures  : le renouvellement donne une nouvelle paire, révoque l'ancien jeton et garde la session.
async def test_refresh_rotates_tokens(
    protected_app: FastAPI, isolated_client: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    await create_account(isolated_client)
    first = await login_tokens(isolated_client)

    response = await refresh(isolated_client, first["refresh_token"])

    assert response.status_code == HTTPStatus.OK
    assert response.headers["cache-control"] == "no-store"
    second = response.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert claims_of(second["access_token"])["sid"] == claims_of(first["access_token"])["sid"]
    assert await protected_status(isolated_client, second["access_token"]) == HTTPStatus.OK
    reasons = (
        (
            await db_session.execute(
                text("SELECT revocation_reason FROM refresh_tokens ORDER BY created_at, revoked_at")
            )
        )
        .scalars()
        .all()
    )
    assert sorted(reasons, key=str) == sorted(["rotated", None], key=str)


# requires : PostgreSQL est démarré et migré.
# assigns  : renouvelle puis réutilise un jeton, dans une transaction annulée à la fin du test.
# ensures  : réutiliser un jeton déjà échangé coupe toute la session, immédiatement, y compris
#            les jetons d'accès déjà émis, et l'événement est journalisé.
async def test_reused_refresh_token_revokes_whole_session(
    protected_app: FastAPI,
    isolated_client: httpx2.AsyncClient,
    db_session: AsyncSession,
    read_logs: LogReader,
) -> None:
    await create_account(isolated_client)
    stolen = await login_tokens(isolated_client)
    legitimate = (await refresh(isolated_client, stolen["refresh_token"])).json()

    reuse = await refresh(isolated_client, stolen["refresh_token"])

    assert reuse.status_code == HTTPStatus.UNAUTHORIZED
    assert (await refresh(isolated_client, legitimate["refresh_token"])).status_code == (
        HTTPStatus.UNAUTHORIZED
    )
    assert await protected_status(isolated_client, legitimate["access_token"]) == (
        HTTPStatus.UNAUTHORIZED
    )
    active = (
        await db_session.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL")
        )
    ).scalar_one()
    assert active == 0
    events = [event for event in read_logs() if event["message"] == "refresh_token_reused"]
    assert len(events) == 1
    assert stolen["refresh_token"] not in str(read_logs())


# requires : PostgreSQL est démarré et migré.
# assigns  : modifie les dates d'une session dans une transaction annulée à la fin du test.
# ensures  : un jeton expiré, par inactivité ou par durée maximale, est refusé sans couper
#            la session.
@pytest.mark.parametrize(
    "update",
    [
        "UPDATE refresh_tokens SET created_at = now() - interval '8 days', "
        "expires_at = now() - interval '1 day'",
        "UPDATE refresh_tokens SET created_at = now() - interval '31 days', "
        "expires_at = now() - interval '1 day', session_expires_at = now() - interval '1 day'",
    ],
)
async def test_expired_refresh_token_is_refused(
    isolated_client: httpx2.AsyncClient, db_session: AsyncSession, update: str
) -> None:
    await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)
    await db_session.execute(text(update))

    assert (await refresh(isolated_client, tokens["refresh_token"])).status_code == (
        HTTPStatus.UNAUTHORIZED
    )
    reasons = (
        (await db_session.execute(text("SELECT revocation_reason FROM refresh_tokens")))
        .scalars()
        .all()
    )
    assert reasons == [None]


# requires : `payload` est un corps de renouvellement invalide.
# assigns  : rien.
# ensures  : un jeton mal formé est refusé en 422 avant toute requête en base, un jeton bien formé
#            mais inconnu en 401.
@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"refresh_token": secrets.token_urlsafe(32)}, HTTPStatus.UNAUTHORIZED),
        ({"refresh_token": "trop-court"}, HTTPStatus.UNPROCESSABLE_ENTITY),
        ({"refresh_token": "!" * 43}, HTTPStatus.UNPROCESSABLE_ENTITY),
        (
            {"refresh_token": secrets.token_urlsafe(32), "user_id": "x"},
            HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        ({}, HTTPStatus.UNPROCESSABLE_ENTITY),
    ],
)
async def test_invalid_refresh_requests_are_refused(
    isolated_client: httpx2.AsyncClient, payload: dict[str, object], status: int
) -> None:
    assert (await isolated_client.post(REFRESH_URL, json=payload)).status_code == status


# requires : PostgreSQL est démarré et migré.
# assigns  : ouvre deux sessions puis en ferme une, dans une transaction annulée à la fin du test.
# ensures  : la déconnexion coupe immédiatement sa session, et seulement elle.
async def test_logout_closes_only_current_session(
    protected_app: FastAPI, isolated_client: httpx2.AsyncClient
) -> None:
    await create_account(isolated_client)
    laptop = await login_tokens(isolated_client)
    phone = await login_tokens(isolated_client)

    response = await isolated_client.post(LOGOUT_URL, headers=bearer(laptop["access_token"]))

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert (
        await protected_status(isolated_client, laptop["access_token"]) == HTTPStatus.UNAUTHORIZED
    )
    assert (await refresh(isolated_client, laptop["refresh_token"])).status_code == (
        HTTPStatus.UNAUTHORIZED
    )
    assert await protected_status(isolated_client, phone["access_token"]) == HTTPStatus.OK
    assert (await refresh(isolated_client, phone["refresh_token"])).status_code == HTTPStatus.OK


# requires : PostgreSQL est démarré et migré.
# assigns  : ouvre deux sessions puis les ferme toutes, dans une transaction annulée à la fin.
# ensures  : la déconnexion globale coupe toutes les sessions et incrémente `token_version`.
async def test_logout_all_closes_every_session(
    protected_app: FastAPI, isolated_client: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    await create_account(isolated_client)
    laptop = await login_tokens(isolated_client)
    phone = await login_tokens(isolated_client)

    response = await isolated_client.post(LOGOUT_ALL_URL, headers=bearer(phone["access_token"]))

    assert response.status_code == HTTPStatus.NO_CONTENT
    for tokens in (laptop, phone):
        assert await protected_status(isolated_client, tokens["access_token"]) == (
            HTTPStatus.UNAUTHORIZED
        )
        assert (await refresh(isolated_client, tokens["refresh_token"])).status_code == (
            HTTPStatus.UNAUTHORIZED
        )
    version = (await db_session.execute(text("SELECT token_version FROM users"))).scalar_one()
    assert version == 1


# requires : `url` est une route protégée par le routeur de session.
# assigns  : rien.
# ensures  : sans jeton, avec un jeton expiré ou falsifié, la route répond 401 sans rien modifier.
@pytest.mark.parametrize("url", [LOGOUT_URL, LOGOUT_ALL_URL])
async def test_session_routes_require_authentication(
    isolated_client: httpx2.AsyncClient, db_session: AsyncSession, url: str
) -> None:
    await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)
    valid = claims_of(tokens["access_token"])
    expired = {**valid, "exp": int(datetime.now(UTC).timestamp()) - 1}

    for headers in [
        {},
        bearer(jwt.encode(expired, TEST_JWT_SECRET, algorithm="HS256")),
        bearer(jwt.encode(valid, secrets.token_hex(32), algorithm="HS256")),
    ]:
        assert (await isolated_client.post(url, headers=headers)).status_code == (
            HTTPStatus.UNAUTHORIZED
        )

    active = (
        await db_session.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NULL")
        )
    ).scalar_one()
    assert active == 1


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte réellement validé en base, supprimé à la fin du test.
# ensures  : fournit un client sans session isolée, et l'identifiant du compte créé.
@pytest.fixture
async def committed_account(
    database_client: httpx2.AsyncClient, app_engine: AsyncEngine
) -> AsyncIterator[tuple[httpx2.AsyncClient, str]]:
    email = f"concurrence-{uuid.uuid4().hex[:12]}@exemple.org"
    account_id = await create_account(database_client, email=email)
    try:
        yield database_client, email
    finally:
        # Suppression ciblée du seul compte créé par ce test ; ses sessions suivent par cascade.
        async with app_engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": account_id})


# requires : PostgreSQL est démarré et migré.
# assigns  : envoie deux renouvellements simultanés du même jeton, sur deux connexions réelles.
# ensures  : un seul réussit ; le second est traité comme une réutilisation et coupe la session,
#            ce qui prouve que le verrou `FOR UPDATE` sérialise bien les deux transactions.
async def test_concurrent_refreshes_are_serialized(
    committed_account: tuple[httpx2.AsyncClient, str],
) -> None:
    client, email = committed_account
    tokens = await login_tokens(client, email=email)

    responses = await asyncio.gather(
        refresh(client, tokens["refresh_token"]), refresh(client, tokens["refresh_token"])
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [HTTPStatus.OK, HTTPStatus.UNAUTHORIZED]
    winner = next(r for r in responses if r.status_code == HTTPStatus.OK).json()
    assert (await refresh(client, winner["refresh_token"])).status_code == HTTPStatus.UNAUTHORIZED


# requires : rien.
# assigns  : rien.
# ensures  : la durée de vie du jeton d'accès est bien de 15 minutes.
def test_access_token_lifetime_is_fifteen_minutes() -> None:
    assert timedelta(minutes=15) == ACCESS_TOKEN_TTL
