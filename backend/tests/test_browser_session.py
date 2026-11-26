"""Session du navigateur : cookie du jeton de renouvellement et protection CSRF.

Le jeton de renouvellement ne voyage plus que dans un cookie `HttpOnly`, et les routes qui le
posent ou le lisent refusent toute requête qui ne vient pas du frontend.
"""

from http import HTTPStatus

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.browser_session import (
    CSRF_HEADER_NAME,
    CSRF_HEADER_VALUE,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
)
from tests.conftest import (
    BROWSER_HEADERS,
    FRONTEND_ORIGIN,
    LOGOUT_ALL_URL,
    LOGOUT_URL,
    REFRESH_URL,
    LogReader,
    OutboxClient,
    bearer,
    create_account,
    login_tokens,
    post_login,
    post_refresh,
    refresh_cookie_of,
)

pytestmark = pytest.mark.anyio

FOREIGN_ORIGIN = "https://attaquant.exemple.org"
# Sept jours d'inactivité au plus (`refresh_token_idle_days` par défaut), en secondes.
IDLE_SECONDS = 7 * 24 * 3600


# requires : `response` contient un en-tête `Set-Cookie` pour le cookie de renouvellement.
# assigns  : rien.
# ensures  : renvoie les attributs de ce cookie, en minuscules, nom → valeur (`""` si sans valeur).
def cookie_attributes(response: httpx2.Response) -> dict[str, str]:
    header = next(
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith(f"{REFRESH_COOKIE_NAME}=")
    )
    attributes: dict[str, str] = {}
    for part in header.split(";")[1:]:
        name, _, value = part.strip().partition("=")
        attributes[name.lower()] = value
    return attributes


# requires : `response` vient de l'API.
# assigns  : rien.
# ensures  : vrai si la réponse demande au navigateur d'effacer le cookie de renouvellement.
def clears_cookie(response: httpx2.Response) -> bool:
    attributes = cookie_attributes(response)
    return attributes.get("max-age") == "0" and attributes.get("path") == REFRESH_COOKIE_PATH


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et ouvre une session, annulés à la fin du test.
# ensures  : la connexion pose le cookie avec tous ses attributs de sécurité, et le jeton de
#            renouvellement n'apparaît plus dans le corps.
async def test_login_sets_hardened_refresh_cookie(isolated_client: OutboxClient) -> None:
    await create_account(isolated_client)

    response = await post_login(isolated_client)

    assert response.status_code == HTTPStatus.OK
    assert set(response.json()) == {"access_token", "token_type", "expires_in"}
    attributes = cookie_attributes(response)
    assert "httponly" in attributes
    assert "secure" in attributes
    assert attributes["samesite"].lower() == "strict"
    assert attributes["path"] == REFRESH_COOKIE_PATH
    assert "domain" not in attributes
    assert 0 < int(attributes["max-age"]) <= IDLE_SECONDS


# requires : rien.
# assigns  : rien.
# ensures  : le chemin du cookie est exactement celui de la route de renouvellement : un chemin
#            décalé empêcherait le navigateur de l'envoyer.
async def test_cookie_path_matches_refresh_route(database_app: FastAPI) -> None:
    assert database_app.url_path_for("refresh") == REFRESH_COOKIE_PATH == REFRESH_URL


# requires : PostgreSQL est démarré et migré.
# assigns  : renouvelle une session dans une transaction annulée à la fin du test.
# ensures  : le renouvellement lit le cookie, en pose un nouveau, et ne renvoie aucun jeton de
#            renouvellement dans le corps.
async def test_refresh_reads_and_rotates_cookie(isolated_client: OutboxClient) -> None:
    await create_account(isolated_client)
    first = await login_tokens(isolated_client)

    response = await post_refresh(isolated_client, first["refresh_token"])

    assert response.status_code == HTTPStatus.OK
    assert "refresh_token" not in response.json()
    assert refresh_cookie_of(response) != first["refresh_token"]
    assert "httponly" in cookie_attributes(response)


# requires : PostgreSQL est démarré et migré.
# assigns  : ouvre une session dans une transaction annulée à la fin du test.
# ensures  : l'ancien flux, jeton dans le corps JSON, n'existe plus : sans cookie, c'est un `401`
#            qui efface le cookie, et le jeton n'est pas consommé.
async def test_refresh_ignores_token_in_body(isolated_client: OutboxClient) -> None:
    await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)

    response = await isolated_client.post(
        REFRESH_URL, json={"refresh_token": tokens["refresh_token"]}, headers=BROWSER_HEADERS
    )

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert clears_cookie(response)
    assert (await post_refresh(isolated_client, tokens["refresh_token"])).status_code == (
        HTTPStatus.OK
    )


# requires : PostgreSQL est démarré et migré.
# assigns  : rien d'autre que la requête testée.
# ensures  : un cookie absent, mal formé ou inconnu donne le même `401`, qui efface le cookie.
@pytest.mark.parametrize("cookie", [None, "", "trop-court", "x" * 43, "+" * 43, "a" * 4000])
async def test_missing_or_invalid_cookie_is_refused(
    isolated_client: OutboxClient, cookie: str | None
) -> None:
    response = await post_refresh(isolated_client, cookie)

    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.json()["detail"] == "Session invalide ou expirée."
    assert clears_cookie(response)


# requires : PostgreSQL est démarré et migré.
# assigns  : ouvre une session dans une transaction annulée à la fin du test.
# ensures  : sans en-tête personnalisé, avec une origine étrangère ou sans origine, le
#            renouvellement est refusé (`403`), journalisé, et le jeton n'est pas consommé.
@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"Origin": FRONTEND_ORIGIN}, "missing_header"),
        ({CSRF_HEADER_NAME: "0", "Origin": FRONTEND_ORIGIN}, "missing_header"),
        ({CSRF_HEADER_NAME: CSRF_HEADER_VALUE, "Origin": FOREIGN_ORIGIN}, "foreign_origin"),
        ({CSRF_HEADER_NAME: CSRF_HEADER_VALUE, "Origin": "null"}, "foreign_origin"),
        ({CSRF_HEADER_NAME: CSRF_HEADER_VALUE}, "missing_origin"),
    ],
)
async def test_refresh_rejects_cross_site_requests(
    isolated_client: OutboxClient,
    db_session: AsyncSession,
    read_logs: LogReader,
    headers: dict[str, str],
    reason: str,
) -> None:
    await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)

    response = await post_refresh(isolated_client, tokens["refresh_token"], headers=headers)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.json()["detail"] == "Requête refusée."
    revoked = (
        await db_session.execute(
            text("SELECT count(*) FROM refresh_tokens WHERE revoked_at IS NOT NULL")
        )
    ).scalar_one()
    assert revoked == 0
    events = [event for event in read_logs() if event["message"] == "csrf_rejected"]
    assert [event["reason"] for event in events] == [reason]
    assert FOREIGN_ORIGIN not in str(read_logs())
    assert tokens["refresh_token"] not in str(read_logs())


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte, annulé à la fin du test.
# ensures  : la connexion exige l'en-tête personnalisé et refuse une origine étrangère, sans
#            poser de cookie ni ouvrir de session.
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": FRONTEND_ORIGIN},
        {CSRF_HEADER_NAME: CSRF_HEADER_VALUE, "Origin": FOREIGN_ORIGIN},
    ],
)
async def test_login_rejects_cross_site_requests(
    isolated_client: OutboxClient, db_session: AsyncSession, headers: dict[str, str]
) -> None:
    await create_account(isolated_client)

    response = await post_login(isolated_client, headers=headers)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "set-cookie" not in response.headers
    sessions = (await db_session.execute(text("SELECT count(*) FROM refresh_tokens"))).scalar_one()
    assert sessions == 0


# requires : PostgreSQL est démarré et migré.
# assigns  : ouvre une session, annulée à la fin du test.
# ensures  : sans origine (outil hors navigateur), la connexion reste possible avec l'en-tête.
async def test_login_without_origin_is_allowed(isolated_client: OutboxClient) -> None:
    await create_account(isolated_client)

    response = await post_login(isolated_client, headers={CSRF_HEADER_NAME: CSRF_HEADER_VALUE})

    assert response.status_code == HTTPStatus.OK


# requires : PostgreSQL est démarré et migré.
# assigns  : ouvre puis ferme une session, annulées à la fin du test.
# ensures  : la déconnexion, simple ou globale, efface le cookie de renouvellement.
@pytest.mark.parametrize("url", [LOGOUT_URL, LOGOUT_ALL_URL])
async def test_logout_clears_refresh_cookie(isolated_client: OutboxClient, url: str) -> None:
    await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)

    response = await isolated_client.post(url, headers=bearer(tokens["access_token"]))

    assert response.status_code == HTTPStatus.NO_CONTENT
    assert clears_cookie(response)
    assert (await post_refresh(isolated_client, tokens["refresh_token"])).status_code == (
        HTTPStatus.UNAUTHORIZED
    )
