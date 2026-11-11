"""Tests de bout en bout de la protection contre les abus, sur PostgreSQL et Redis réels."""

import asyncio
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Protocol

import httpx2
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Receive, Scope, Send

from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings
from app.db.session import SessionFactory, get_session, get_session_factory
from app.main import create_app
from app.modules.auth.dependencies import AUTHENTICATED_DEPENDENCIES
from app.modules.auth.limits import TOO_MANY_REQUESTS_DETAIL
from tests.conftest import (
    LOGIN_URL,
    LOGOUT_URL,
    REFRESH_URL,
    REGISTER_URL,
    TEST_PASSPHRASE,
    DatabaseTestSettings,
    LogReader,
    OutboxClient,
    RecordingEmailSender,
    bearer,
    borrowing_factory,
    create_account,
    unique_rate_limit_prefix,
    unreachable_redis_url,
)

pytestmark = pytest.mark.anyio

ATTACKER_IP = "203.0.113.66"
VICTIM_IP = "198.51.100.10"
EMAIL = "alice@exemple.org"
WRONG_PASSWORD = "mauvais mot de passe 2026"
PROBLEM_MEDIA_TYPE = "application/problem+json"
PROTECTED_URL = "/protected"


class AppBuilder(Protocol):
    """Fabrique d'applications de test, aux limites choisies par chaque test."""

    # requires : `env` contient des variables `TASKHANDLER_*` valides.
    # assigns  : fixe ces variables le temps du test et crée une application.
    # ensures  : renvoie une application reliée à PostgreSQL et Redis, dont les écritures en base
    #            sont annulées à la fin du test.
    def __call__(
        self, *, env: dict[str, str] | None = None, redis_url: SecretStr | None = None
    ) -> FastAPI: ...


# requires : la base de test est migrée et Redis est démarré.
# assigns  : crée des applications, fermées à la fin du test.
# ensures  : fournit une fabrique d'applications ; toutes partagent la session isolée du test.
@pytest.fixture
async def build_app(
    migrated_database: None,
    database_settings: DatabaseTestSettings,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AppBuilder]:
    created: list[FastAPI] = []
    factory = borrowing_factory(db_session)

    # requires : rien.
    # assigns  : annule la transaction en cours si la route lève une erreur.
    # ensures  : fournit la session isolée du test à la place d'une nouvelle session, avec la même
    #            annulation automatique que `get_session` en production.
    async def use_test_session() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
        except Exception:
            await db_session.rollback()
            raise

    # requires : voir `AppBuilder.__call__`.
    # assigns  : voir `AppBuilder.__call__`.
    # ensures  : voir `AppBuilder.__call__`.
    def build(*, env: dict[str, str] | None = None, redis_url: SecretStr | None = None) -> FastAPI:
        for name, value in (env or {}).items():
            monkeypatch.setenv(name, value)
        application = create_app(
            Settings(
                environment="test",
                database_url=database_settings.app_database_url,
                redis_url=redis_url if redis_url is not None else database_settings.redis_url,
                rate_limit_key_prefix=unique_rate_limit_prefix(),
                _env_file=None,
            )
        )
        application.dependency_overrides[get_session] = use_test_session
        application.dependency_overrides[get_session_factory] = lambda: factory
        application.state.email_sender = RecordingEmailSender()
        created.append(application)
        return application

    yield build
    for application in created:
        await application.state.engine.dispose()
        await application.state.redis.aclose()


# requires : `build_app` est disponible.
# assigns  : crée une application aux limites par défaut.
# ensures  : fournit l'application ; demandée avant `read_logs`, elle est créée avant la capture
#            des journaux, que sa configuration des journaux effacerait sinon.
@pytest.fixture
def default_app(build_app: AppBuilder) -> FastAPI:
    return build_app()


# requires : `build_app` est disponible.
# assigns  : crée une application normale et une application privée de Redis.
# ensures  : fournit les deux applications, créées avant la capture des journaux.
@pytest.fixture
def healthy_and_degraded_apps(build_app: AppBuilder) -> tuple[FastAPI, FastAPI]:
    return build_app(), build_app(redis_url=unreachable_redis_url())


# requires : `application` est assemblée.
# assigns  : rien.
# ensures  : renvoie un client dont toutes les requêtes viennent de l'IP `ip`, et qui voit les
#            e-mails envoyés par l'application.
def client_from(application: FastAPI, ip: str) -> OutboxClient:
    transport = httpx2.ASGITransport(
        app=application, raise_app_exceptions=False, client=(ip, 50_000)
    )
    factory: SessionFactory = application.dependency_overrides[get_session_factory]()
    return OutboxClient(
        transport=transport,
        outbox=application.state.email_sender,
        session_factory=factory,
    )


# requires : `client` est relié à l'application de test.
# assigns  : envoie une tentative de connexion.
# ensures  : renvoie la réponse HTTP brute.
async def attempt(
    client: httpx2.AsyncClient,
    password: str = WRONG_PASSWORD,
    headers: dict[str, str] | None = None,
) -> httpx2.Response:
    return await client.post(
        LOGIN_URL, json={"email": EMAIL, "password": password}, headers=headers
    )


# requires : `client` est relié à l'application de test.
# assigns  : crée le compte de la victime et vérifie son adresse.
# ensures  : le compte existe et peut se connecter ; échoue sinon.
async def register_victim(client: OutboxClient) -> None:
    await create_account(client, email=EMAIL, password=TEST_PASSPHRASE)


# requires : `response` est un refus pour cause de limite.
# assigns  : rien.
# ensures  : le refus est un 429 RFC 9457, avec un `Retry-After` en secondes entières positives.
def assert_rate_limited(response: httpx2.Response, max_retry_after: int) -> None:
    assert response.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == TOO_MANY_REQUESTS_DETAIL
    assert 1 <= int(response.headers["retry-after"]) <= max_retry_after


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte et simule une attaque, dans une transaction annulée à la fin.
# ensures  : critère de fin de la phase : la force brute est bloquée, sans que la victime, depuis
#            sa propre IP, ne soit gênée le moins du monde.
async def test_brute_force_is_blocked_without_locking_victim(
    default_app: FastAPI, read_logs: LogReader
) -> None:
    application = default_app
    async with (
        client_from(application, ATTACKER_IP) as attacker,
        client_from(application, VICTIM_IP) as victim,
    ):
        await register_victim(victim)

        for _ in range(5):
            assert (await attempt(attacker)).status_code == HTTPStatus.UNAUTHORIZED
        blocked = await attempt(attacker)
        assert_rate_limited(blocked, max_retry_after=60)
        # Même le bon mot de passe ne passe plus depuis l'IP de l'attaquant.
        assert_rate_limited(await attempt(attacker, TEST_PASSPHRASE), max_retry_after=60)

        assert (await attempt(victim, TEST_PASSPHRASE)).status_code == HTTPStatus.OK

    events = [event for event in read_logs() if event["message"] == "rate_limited"]
    assert events
    assert ATTACKER_IP not in str(events)
    assert EMAIL not in str(events)


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte et enchaîne des échecs, dans une transaction annulée à la fin.
# ensures  : après 5 échecs, le délai double à chaque échec (1 s, puis 2 s), se libère de lui-même,
#            et une connexion réussie efface la temporisation.
async def test_login_backoff_is_progressive_and_temporary(build_app: AppBuilder) -> None:
    application = build_app(env={"TASKHANDLER_LOGIN_ACCOUNT_LIMIT_PER_MINUTE": "100"})
    async with client_from(application, ATTACKER_IP) as client:
        await register_victim(client)
        for _ in range(5):
            assert (await attempt(client)).status_code == HTTPStatus.UNAUTHORIZED

        first = await attempt(client)
        assert_rate_limited(first, max_retry_after=1)
        await asyncio.sleep(1.1)
        assert (await attempt(client)).status_code == HTTPStatus.UNAUTHORIZED
        second = await attempt(client)
        assert int(second.headers["retry-after"]) == 2

        await asyncio.sleep(2.1)
        assert (await attempt(client, TEST_PASSPHRASE)).status_code == HTTPStatus.OK
        assert (await attempt(client)).status_code == HTTPStatus.UNAUTHORIZED


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : envoie des connexions pour plusieurs adresses depuis une même IP.
# ensures  : une IP qui essaie beaucoup de comptes différents est elle aussi limitée.
async def test_login_is_limited_per_ip(build_app: AppBuilder) -> None:
    application = build_app(env={"TASKHANDLER_LOGIN_IP_LIMIT_PER_MINUTE": "3"})
    async with client_from(application, ATTACKER_IP) as client:
        for index in range(3):
            response = await client.post(
                LOGIN_URL, json={"email": f"cible{index}@exemple.org", "password": WRONG_PASSWORD}
            )
            assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert_rate_limited(await attempt(client), max_retry_after=60)


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : envoie des connexions avec un `X-Forwarded-For` différent à chaque fois.
# ensures  : sans proxy de confiance, changer l'en-tête ne contourne pas la limite.
async def test_forged_forwarded_for_does_not_bypass_limits(build_app: AppBuilder) -> None:
    application = build_app()
    async with client_from(application, ATTACKER_IP) as client:
        await register_victim(client)
        for index in range(5):
            spoofed = {"X-Forwarded-For": f"192.0.2.{index}"}
            assert (await attempt(client, headers=spoofed)).status_code == HTTPStatus.UNAUTHORIZED

        response = await attempt(client, headers={"X-Forwarded-For": "192.0.2.99"})

    assert_rate_limited(response, max_retry_after=60)


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : envoie des connexions par un proxy déclaré de confiance.
# ensures  : derrière un proxy de confiance, chaque client est compté séparément : la limite
#            d'un client ne pénalise pas les autres clients du même proxy.
async def test_trusted_proxy_separates_clients(build_app: AppBuilder) -> None:
    application = build_app(env={"TASKHANDLER_TRUSTED_PROXIES": '["203.0.113.0/24"]'})
    async with client_from(application, ATTACKER_IP) as proxy:
        await register_victim(proxy)
        for _ in range(5):
            attacker = {"X-Forwarded-For": "192.0.2.50"}
            assert (await attempt(proxy, headers=attacker)).status_code == HTTPStatus.UNAUTHORIZED
        assert_rate_limited(
            await attempt(proxy, headers={"X-Forwarded-For": "192.0.2.50"}), max_retry_after=60
        )

        victim = await attempt(proxy, TEST_PASSPHRASE, headers={"X-Forwarded-For": "192.0.2.51"})

    assert victim.status_code == HTTPStatus.OK


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : envoie des inscriptions, dans une transaction annulée à la fin.
# ensures  : au-delà de 3 demandes par adresse, l'inscription est refusée ; avant, la réponse est
#            la même `202`, que l'adresse soit déjà inscrite ou non.
async def test_registration_is_limited_per_email(build_app: AppBuilder) -> None:
    application = build_app()
    statuses = []
    for index in range(4):
        async with client_from(application, f"198.51.100.{index + 20}") as client:
            body = {"email": EMAIL, "password": TEST_PASSPHRASE}
            statuses.append((await client.post(REGISTER_URL, json=body)).status_code)

    assert statuses[:3] == [HTTPStatus.ACCEPTED] * 3
    assert statuses[3] == HTTPStatus.TOO_MANY_REQUESTS


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : envoie des inscriptions, dans une transaction annulée à la fin.
# ensures  : au-delà de 5 inscriptions par heure depuis une IP, l'inscription est refusée.
async def test_registration_is_limited_per_ip(build_app: AppBuilder) -> None:
    application = build_app()
    async with client_from(application, ATTACKER_IP) as client:
        for index in range(5):
            body = {"email": f"masse{index}@exemple.org", "password": TEST_PASSPHRASE}
            assert (await client.post(REGISTER_URL, json=body)).status_code == HTTPStatus.ACCEPTED
        body = {"email": "masse5@exemple.org", "password": TEST_PASSPHRASE}
        assert_rate_limited(await client.post(REGISTER_URL, json=body), max_retry_after=3600)


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : ouvre une session et la renouvelle, dans une transaction annulée à la fin.
# ensures  : au-delà de la limite par utilisateur, le renouvellement est refusé en 429.
async def test_refresh_is_limited_per_user(build_app: AppBuilder) -> None:
    application = build_app(env={"TASKHANDLER_REFRESH_USER_LIMIT_PER_MINUTE": "2"})
    async with client_from(application, VICTIM_IP) as client:
        await register_victim(client)
        tokens = (await attempt(client, TEST_PASSPHRASE)).json()
        for _ in range(2):
            response = await client.post(
                REFRESH_URL, json={"refresh_token": tokens["refresh_token"]}
            )
            assert response.status_code == HTTPStatus.OK
            tokens = response.json()

        response = await client.post(REFRESH_URL, json={"refresh_token": tokens["refresh_token"]})

    assert_rate_limited(response, max_retry_after=60)


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : ajoute une route protégée et l'appelle, dans une transaction annulée à la fin.
# ensures  : les routes authentifiées sont limitées par utilisateur, au niveau du routeur.
async def test_authenticated_routes_are_limited_per_user(build_app: AppBuilder) -> None:
    application = build_app(env={"TASKHANDLER_AUTHENTICATED_USER_LIMIT_PER_MINUTE": "2"})

    # requires : un jeton valide et la limite respectée (vérifiés par les dépendances).
    # assigns  : rien.
    # ensures  : renvoie une réponse vide.
    async def protected() -> dict[str, str]:
        return {}

    application.add_api_route(PROTECTED_URL, protected, dependencies=AUTHENTICATED_DEPENDENCIES)
    async with client_from(application, VICTIM_IP) as client:
        await register_victim(client)
        access = (await attempt(client, TEST_PASSPHRASE)).json()["access_token"]
        for _ in range(2):
            assert (await client.get(PROTECTED_URL, headers=bearer(access))).status_code == 200

        assert_rate_limited(
            await client.get(PROTECTED_URL, headers=bearer(access)), max_retry_after=60
        )


# requires : PostgreSQL est démarré, Redis est injoignable pour la seconde application.
# assigns  : ouvre une session avec Redis, puis utilise une application privée de Redis.
# ensures  : sans Redis, inscription, connexion et renouvellement sont refusés (503), mais la
#            déconnexion reste possible : une panne ne doit pas empêcher de couper une session.
async def test_redis_outage_closes_auth_but_not_logout(
    healthy_and_degraded_apps: tuple[FastAPI, FastAPI], read_logs: LogReader
) -> None:
    healthy_app, degraded_app = healthy_and_degraded_apps
    async with client_from(healthy_app, VICTIM_IP) as healthy:
        await register_victim(healthy)
        tokens = (await attempt(healthy, TEST_PASSPHRASE)).json()

    async with client_from(degraded_app, VICTIM_IP) as degraded:
        register = await degraded.post(
            REGISTER_URL, json={"email": "nouveau@exemple.org", "password": TEST_PASSPHRASE}
        )
        login = await attempt(degraded, TEST_PASSPHRASE)
        refresh = await degraded.post(REFRESH_URL, json={"refresh_token": tokens["refresh_token"]})
        logout = await degraded.post(LOGOUT_URL, headers=bearer(tokens["access_token"]))

    assert register.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert login.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert refresh.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert logout.status_code == HTTPStatus.NO_CONTENT
    assert any(event["message"] == "rate_limit_unavailable" for event in read_logs())


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : envoie des corps trop volumineux.
# ensures  : un corps annoncé trop grand, ou envoyé par morceaux sans taille annoncée, est refusé
#            en 413 au format RFC 9457, avant toute validation.
async def test_oversized_bodies_are_rejected(build_app: AppBuilder) -> None:
    application = build_app(env={"TASKHANDLER_MAX_REQUEST_BODY_BYTES": "1024"})
    payload = b'{"email": "' + b"a" * 2000 + b'@exemple.org", "password": "x"}'

    # requires : rien.
    # assigns  : rien.
    # ensures  : produit le corps en plusieurs morceaux, sans en-tête `Content-Length`.
    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(payload), 256):
            yield payload[start : start + 256]

    async with client_from(application, VICTIM_IP) as client:
        declared = await client.post(
            REGISTER_URL, content=payload, headers={"content-type": "application/json"}
        )
        streamed = await client.post(
            REGISTER_URL, content=chunks(), headers={"content-type": "application/json"}
        )

    for response in (declared, streamed):
        assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE


# requires : rien.
# assigns  : rien.
# ensures  : un `Content-Length` illisible est refusé en 400, sans appeler l'application.
async def test_invalid_content_length_is_rejected() -> None:
    reached = False

    # requires : les arguments respectent le protocole ASGI.
    # assigns  : marque l'application comme atteinte.
    # ensures  : ne devrait jamais être appelée dans ce test.
    async def application(_scope: Scope, _receive: Receive, _send: Send) -> None:
        nonlocal reached
        reached = True

    sent: list[Message] = []

    # requires : rien.
    # assigns  : rien.
    # ensures  : renvoie un corps vide, comme un client qui n'envoie rien.
    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    # requires : `message` est un message ASGI de réponse.
    # assigns  : ajoute le message à `sent`.
    # ensures  : rien d'autre.
    async def send(message: Message) -> None:
        sent.append(message)

    middleware = BodySizeLimitMiddleware(application, max_body_bytes=1024)
    scope: Scope = {"type": "http", "headers": [(b"content-length", b"12abc")]}
    await middleware(scope, receive, send)

    assert reached is False
    assert sent[0]["status"] == HTTPStatus.BAD_REQUEST
