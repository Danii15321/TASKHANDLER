"""Tests de `POST /api/v1/auth/login`, sur un vrai PostgreSQL."""

from collections.abc import Callable
from http import HTTPStatus

import httpx2
import jwt
import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import PasswordHasherService, PasswordHashingBusyError
from app.core.tokens import hash_opaque_token
from tests.conftest import (
    LOGIN_URL,
    TEST_PASSPHRASE,
    LogReader,
    OutboxClient,
    create_account,
    login_tokens,
)

pytestmark = pytest.mark.anyio

EMAIL = "alice@exemple.org"
TOKEN_FIELDS = {"access_token", "refresh_token", "token_type", "expires_in"}
REQUIRED_HASH_PREFIX = "$argon2id$v=19$m=65536,t=3,p=4$"


# requires : `client` est relié à l'application de test.
# assigns  : envoie une demande de connexion.
# ensures  : renvoie la réponse HTTP brute.
async def attempt_login(
    client: httpx2.AsyncClient, email: str = EMAIL, password: str = TEST_PASSPHRASE
) -> httpx2.Response:
    return await client.post(LOGIN_URL, json={"email": email, "password": password})


# requires : `response` est une réponse d'erreur au format RFC 9457.
# assigns  : rien.
# ensures  : renvoie le corps sans l'identifiant de requête, propre à chaque réponse.
def comparable(response: httpx2.Response) -> dict[str, object]:
    body: dict[str, object] = response.json()
    body.pop("request_id")
    return body


# requires : `hasher` est le service de l'application de test.
# assigns  : remplace le temps du test la méthode interne de calcul par une version qui compte.
# ensures  : renvoie une fonction qui donne le nombre de calculs argon2id effectués.
def count_argon2_operations(
    hasher: PasswordHasherService, monkeypatch: pytest.MonkeyPatch
) -> Callable[[], int]:
    calls: list[int] = []
    original = hasher._run_bounded

    # requires : mêmes arguments que la méthode d'origine.
    # assigns  : ajoute une entrée à `calls`.
    # ensures  : renvoie le résultat de la méthode d'origine.
    async def counting(job: Callable[[], object]) -> object:
        calls.append(1)
        return await original(job)

    monkeypatch.setattr(hasher, "_run_bounded", counting)
    return lambda: len(calls)


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : la connexion renvoie une paire de jetons conforme, jamais mise en cache.
async def test_login_returns_tokens(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    account_id = await create_account(isolated_client)

    response = await attempt_login(isolated_client)

    assert response.status_code == HTTPStatus.OK
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == TOKEN_FIELDS
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    claims = jwt.decode(body["access_token"], options={"verify_signature": False})
    assert claims["sub"] == account_id
    assert EMAIL not in str(claims)


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : la base ne contient que l'empreinte du jeton de renouvellement, jamais sa valeur.
async def test_refresh_token_is_stored_hashed(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    await create_account(isolated_client)
    refresh_token = (await login_tokens(isolated_client))["refresh_token"]

    rows = (await db_session.execute(text("SELECT * FROM refresh_tokens"))).mappings().all()

    assert len(rows) == 1
    assert rows[0]["token_hash"] == hash_opaque_token(refresh_token)
    assert refresh_token not in str(dict(rows[0]))


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte, désactive un compte, dans une transaction annulée à la fin du test.
# ensures  : adresse inconnue, mot de passe faux et compte inactif donnent la même réponse, après
#            exactement une vérification argon2id chacun : rien ne distingue les trois cas.
async def test_failures_are_indistinguishable(
    database_app: FastAPI,
    isolated_client: OutboxClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(isolated_client)
    await create_account(isolated_client, email="inactif@exemple.org")
    await db_session.execute(
        text("UPDATE users SET is_active = false WHERE email = 'inactif@exemple.org'")
    )
    # Validée comme une vraie modification : une route en erreur annule sa transaction, et
    # emporterait sinon cette préparation (elle reste annulée à la fin du test).
    await db_session.commit()
    hasher: PasswordHasherService = database_app.state.password_hasher
    await hasher.prepare()
    operations = count_argon2_operations(hasher, monkeypatch)

    responses = []
    for email, password in [
        ("inconnu@exemple.org", TEST_PASSPHRASE),
        (EMAIL, "mauvais mot de passe 2026"),
        ("inactif@exemple.org", TEST_PASSPHRASE),
    ]:
        before = operations()
        responses.append(await attempt_login(isolated_client, email, password))
        assert operations() - before == 1

    assert {response.status_code for response in responses} == {HTTPStatus.UNAUTHORIZED}
    assert all(r.headers["www-authenticate"] == "Bearer" for r in responses)
    assert comparable(responses[0]) == comparable(responses[1]) == comparable(responses[2])
    assert responses[0].json()["detail"] == "Identifiants invalides."


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : l'adresse est normalisée comme à l'inscription : casse et espaces sont ignorés.
async def test_login_normalizes_email(isolated_client: OutboxClient) -> None:
    await create_account(isolated_client)

    response = await attempt_login(isolated_client, email="  ALICE@Exemple.org ")

    assert response.status_code == HTTPStatus.OK


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : le mot de passe est normalisé comme à l'inscription (NFKC).
async def test_login_normalizes_password(isolated_client: OutboxClient) -> None:
    fullwidth = "".join(chr(ord(letter) + 0xFEE0) for letter in "violet") + " ruisseau lanterne"
    await create_account(isolated_client, password=fullwidth)

    response = await attempt_login(isolated_client, password="violet ruisseau lanterne")

    assert response.status_code == HTTPStatus.OK


# requires : PostgreSQL est démarré et migré.
# assigns  : insère un compte haché avec des paramètres faibles, puis se connecte.
# ensures  : l'empreinte est remplacée par une empreinte aux paramètres actuels, toujours valide.
async def test_login_upgrades_outdated_hash(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(TEST_PASSPHRASE)
    await db_session.execute(
        text(
            "INSERT INTO users (email, password_hash, email_verified_at) "
            "VALUES (:email, :hash, now())"
        ),
        {"email": EMAIL, "hash": weak_hash},
    )

    response = await attempt_login(isolated_client)

    assert response.status_code == HTTPStatus.OK
    stored = (
        await db_session.execute(
            text("SELECT password_hash FROM users WHERE email = :email"), {"email": EMAIL}
        )
    ).scalar_one()
    assert stored.startswith(REQUIRED_HASH_PREFIX)
    hasher = PasswordHasherService(max_concurrency=1, queue_timeout_seconds=10)
    assert await hasher.verify(stored, TEST_PASSPHRASE) is True


# requires : `payload` est un corps de connexion invalide.
# assigns  : rien (la demande est refusée avant toute vérification).
# ensures  : la demande est refusée en 422, sans recopier le mot de passe.
@pytest.mark.parametrize(
    "payload",
    [
        {"email": EMAIL},
        {"email": EMAIL, "password": ""},
        {"email": EMAIL, "password": "x" * 129},
        {"email": EMAIL, "password": TEST_PASSPHRASE, "remember_me": True},
        {"email": EMAIL, "password": TEST_PASSPHRASE, "user_id": "x"},
        {"email": "pas-une-adresse", "password": TEST_PASSPHRASE},
    ],
)
async def test_invalid_login_payload_is_refused(
    isolated_client: OutboxClient, payload: dict[str, object]
) -> None:
    response = await isolated_client.post(LOGIN_URL, json=payload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert TEST_PASSPHRASE not in response.text


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte et une session dans une transaction annulée à la fin du test.
# ensures  : les journaux d'audit enregistrent succès et échecs, sans mot de passe, sans jeton et
#            sans adresse e-mail.
async def test_login_is_audited_without_secrets(
    isolated_client: OutboxClient, read_logs: LogReader
) -> None:
    account_id = await create_account(isolated_client)
    tokens = await login_tokens(isolated_client)
    await attempt_login(isolated_client, password="mauvais mot de passe 2026")
    await attempt_login(isolated_client, email="inconnu@exemple.org")

    events = read_logs()
    audit = [
        event
        for event in events
        if event["logger"] == "app.audit" and str(event["message"]).startswith("login_")
    ]
    assert [event["message"] for event in audit] == [
        "login_succeeded",
        "login_failed",
        "login_failed",
    ]
    assert audit[0]["user_id"] == account_id
    assert [event.get("reason") for event in audit[1:]] == ["wrong_password", "unknown_account"]
    logs = str(events)
    for secret in (TEST_PASSPHRASE, tokens["access_token"], tokens["refresh_token"], EMAIL):
        assert secret not in logs


# requires : PostgreSQL est démarré et migré.
# assigns  : remplace la vérification par une saturation simulée le temps du test.
# ensures  : un serveur saturé répond 503 avec `Retry-After`, sans ouvrir de session.
async def test_saturated_hashing_returns_service_unavailable(
    database_app: FastAPI,
    isolated_client: OutboxClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(isolated_client)

    # requires : rien.
    # assigns  : rien.
    # ensures  : lève toujours l'erreur de saturation.
    async def saturated(_password_hash: str, _plain: str) -> bool:
        raise PasswordHashingBusyError

    monkeypatch.setattr(database_app.state.password_hasher, "verify", saturated)

    response = await attempt_login(isolated_client)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.headers["retry-after"] == "5"
    count = (await db_session.execute(text("SELECT count(*) FROM refresh_tokens"))).scalar_one()
    assert count == 0
