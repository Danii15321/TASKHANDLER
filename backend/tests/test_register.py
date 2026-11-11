"""Tests de `POST /api/v1/auth/register`, sur un vrai PostgreSQL."""

from http import HTTPStatus

import httpx2
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import PasswordHasherService, PasswordHashingBusyError
from app.modules.auth.password_policy import MAX_PASSWORD_LENGTH
from tests.conftest import LogReader

pytestmark = pytest.mark.anyio

REGISTER_URL = "/api/v1/auth/register"
PASSPHRASE = "violet ruisseau lanterne 2026"
EMAIL = "alice@exemple.org"
PROBLEM_MEDIA_TYPE = "application/problem+json"
PUBLIC_FIELDS = {"id", "email", "created_at"}
SENSITIVE_MARKERS = ("password", "argon2", "hash", "token_version", "is_active")


# requires : `session` est la session isolée du test.
# assigns  : rien (lecture seule).
# ensures  : renvoie le nombre de comptes visibles dans la transaction du test.
async def count_users(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT count(*) FROM users"))).scalar_one())


# requires : `client` est relié à l'application de test.
# assigns  : envoie une demande d'inscription.
# ensures  : renvoie la réponse HTTP brute.
async def register(
    client: httpx2.AsyncClient, payload: dict[str, object] | None = None
) -> httpx2.Response:
    body = payload if payload is not None else {"email": EMAIL, "password": PASSPHRASE}
    return await client.post(REGISTER_URL, json=body)


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte dans une transaction annulée à la fin du test.
# ensures  : le compte est créé, l'adresse normalisée, et seuls les champs publics sont renvoyés.
async def test_register_creates_account(
    isolated_client: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    response = await register(
        isolated_client, {"email": "  Alice@Exemple.ORG ", "password": PASSPHRASE}
    )

    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert set(body) == PUBLIC_FIELDS
    assert body["email"] == EMAIL

    row = (
        await db_session.execute(
            text(
                "SELECT password_hash, is_active, email_verified_at, token_version "
                "FROM users WHERE id = :id"
            ),
            {"id": body["id"]},
        )
    ).one()
    assert row.is_active is True
    assert row.email_verified_at is None
    assert row.token_version == 0
    hasher = PasswordHasherService(max_concurrency=1, queue_timeout_seconds=10)
    assert await hasher.verify(row.password_hash, PASSPHRASE) is True


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte dans une transaction annulée à la fin du test.
# ensures  : ni le mot de passe, ni son empreinte, ni un champ interne n'apparaissent en réponse.
async def test_response_never_contains_password_or_hash(
    isolated_client: httpx2.AsyncClient,
) -> None:
    response = await register(isolated_client)

    assert response.status_code == HTTPStatus.CREATED
    assert PASSPHRASE not in response.text
    for marker in SENSITIVE_MARKERS:
        assert marker not in response.text


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte dans une transaction annulée à la fin du test.
# ensures  : le mot de passe enregistré est la forme normalisée : la connexion pourra le retrouver.
async def test_password_is_hashed_after_normalization(
    isolated_client: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    # Lettres « pleine chasse » : même dessin que l'ASCII, décalées de 0xFEE0 dans Unicode.
    fullwidth = "".join(chr(ord(letter) + 0xFEE0) for letter in "violet") + " ruisseau lanterne"

    response = await register(isolated_client, {"email": EMAIL, "password": fullwidth})

    stored_hash = (
        await db_session.execute(
            text("SELECT password_hash FROM users WHERE id = :id"), {"id": response.json()["id"]}
        )
    ).scalar_one()
    hasher = PasswordHasherService(max_concurrency=1, queue_timeout_seconds=10)
    assert await hasher.verify(stored_hash, "violet ruisseau lanterne") is True


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte dans une transaction annulée à la fin du test.
# ensures  : une adresse déjà inscrite, quelle que soit sa casse, est refusée sans doublon.
async def test_duplicate_email_is_refused_regardless_of_case(
    isolated_client: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    assert (await register(isolated_client)).status_code == HTTPStatus.CREATED

    response = await register(
        isolated_client, {"email": "ALICE@exemple.org", "password": "autre phrase de passe 99"}
    )

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert await count_users(db_session) == 1


# requires : `extra_field` est une propriété que seul le serveur peut fixer.
# assigns  : rien (la demande est refusée avant toute écriture).
# ensures  : toute tentative d'affectation de masse est refusée (OWASP API3).
@pytest.mark.parametrize(
    "extra_field",
    [
        {"id": "00000000-0000-0000-0000-000000000001"},
        {"user_id": "00000000-0000-0000-0000-000000000001"},
        {"is_active": False},
        {"password_hash": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"},
        {"email_verified_at": "2026-01-01T00:00:00Z"},
        {"token_version": 5},
    ],
)
async def test_mass_assignment_is_refused(
    isolated_client: httpx2.AsyncClient, db_session: AsyncSession, extra_field: dict[str, object]
) -> None:
    response = await register(
        isolated_client, {"email": EMAIL, "password": PASSPHRASE, **extra_field}
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert await count_users(db_session) == 0


# requires : `email` est une adresse invalide.
# assigns  : rien (la demande est refusée avant toute écriture).
# ensures  : l'adresse est refusée, sans être recopiée dans la réponse.
@pytest.mark.parametrize(
    "email",
    ["pas-une-adresse", "alice@localhost", "ali\u0000ce@exemple.org", "a" * 300 + "@x.fr", 42],
)
async def test_invalid_email_is_refused(isolated_client: httpx2.AsyncClient, email: object) -> None:
    response = await register(isolated_client, {"email": email, "password": PASSPHRASE})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "pas-une-adresse" not in response.text


# requires : `password` enfreint la politique de mot de passe.
# assigns  : rien (la demande est refusée avant tout hachage).
# ensures  : le mot de passe est refusé, sans être recopié dans la réponse.
@pytest.mark.parametrize(
    "password",
    [
        "court-11car",
        "asdfasdfasdf",
        "alice-2026-violet",
        "violet\u0000ruisseau-x",
        "x" * MAX_PASSWORD_LENGTH + "y",
    ],
)
async def test_weak_password_is_refused_without_echo(
    isolated_client: httpx2.AsyncClient, db_session: AsyncSession, password: str
) -> None:
    response = await register(isolated_client, {"email": EMAIL, "password": password})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert password not in response.text
    assert await count_users(db_session) == 0


# requires : PostgreSQL est démarré et migré.
# assigns  : crée un compte dans une transaction annulée à la fin du test.
# ensures  : ni le mot de passe ni son empreinte n'apparaissent dans les journaux.
async def test_password_and_hash_are_never_logged(
    isolated_client: httpx2.AsyncClient, read_logs: LogReader
) -> None:
    await register(isolated_client)
    await register(isolated_client, {"email": EMAIL, "password": "asdfasdfasdf"})

    logs = str(read_logs())
    assert PASSPHRASE not in logs
    assert "asdfasdfasdf" not in logs
    assert "$argon2" not in logs


# requires : PostgreSQL est démarré et migré.
# assigns  : remplace le hachage par une saturation simulée le temps du test.
# ensures  : un serveur saturé répond 503 avec `Retry-After`, sans créer de compte.
async def test_saturated_hashing_returns_service_unavailable(
    database_app: FastAPI,
    isolated_client: httpx2.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : lève toujours l'erreur de saturation.
    async def saturated(_plain: str) -> str:
        raise PasswordHashingBusyError

    monkeypatch.setattr(database_app.state.password_hasher, "hash", saturated)

    response = await register(isolated_client)

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.headers["retry-after"] == "5"
    assert await count_users(db_session) == 0


# requires : PostgreSQL est démarré et migré.
# assigns  : remplace le hachage par une empreinte non argon2id le temps du test.
# ensures  : si le hachage était un jour défaillant, la base refuse l'empreinte et l'API ne
#            renvoie qu'une erreur générique, sans détail SQL ni empreinte.
async def test_database_refuses_non_argon2id_hash(
    database_app: FastAPI,
    isolated_client: httpx2.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : renvoie une empreinte bcrypt, interdite par la contrainte de la table.
    async def weak_hash(_plain: str) -> str:
        return "$2b$12$" + "x" * 53

    monkeypatch.setattr(database_app.state.password_hasher, "hash", weak_hash)

    response = await register(isolated_client)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    for detail in ("ck_users", "$2b$", "INSERT", "users"):
        assert detail not in response.text
    assert await count_users(db_session) == 0
