"""Tests de `POST /api/v1/auth/register` : réponse uniforme, travail après la réponse."""

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import PasswordHasherService, PasswordHashingBusyError
from app.modules.auth.password_policy import MAX_PASSWORD_LENGTH
from tests.conftest import (
    REGISTER_URL,
    TEST_PASSPHRASE,
    VERIFY_EMAIL_URL,
    LogReader,
    OutboxClient,
    create_account,
)

pytestmark = pytest.mark.anyio

EMAIL = "alice@exemple.org"
PROBLEM_MEDIA_TYPE = "application/problem+json"
ACCEPTED_BODY = {"status": "accepted"}


# requires : `session` est la session isolée du test.
# assigns  : rien (lecture seule).
# ensures  : renvoie le nombre de comptes visibles dans la transaction du test.
async def count_users(session: AsyncSession) -> int:
    return int((await session.execute(text("SELECT count(*) FROM users"))).scalar_one())


# requires : `client` est relié à l'application de test.
# assigns  : envoie une demande d'inscription (et laisse s'exécuter sa tâche d'arrière-plan).
# ensures  : renvoie la réponse HTTP brute.
async def register(
    client: OutboxClient, payload: dict[str, object] | None = None
) -> tuple[int, dict[str, object], str]:
    body = payload if payload is not None else {"email": EMAIL, "password": TEST_PASSPHRASE}
    response = await client.post(REGISTER_URL, json=body)
    return response.status_code, response.json(), response.text


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte dans une transaction annulée à la fin du test.
# ensures  : la réponse est `202`, et le compte, non vérifié, reçoit un lien de vérification dont
#            le jeton est dans le fragment de l'URL, jamais dans sa requête.
async def test_register_creates_unverified_account(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    status, body, _ = await register(
        isolated_client, {"email": "  Alice@Exemple.ORG ", "password": TEST_PASSPHRASE}
    )

    assert status == HTTPStatus.ACCEPTED
    assert body == ACCEPTED_BODY
    row = (
        await db_session.execute(
            text("SELECT password_hash, email_verified_at, is_active FROM users WHERE email = :e"),
            {"e": EMAIL},
        )
    ).one()
    assert row.email_verified_at is None
    assert row.is_active is True
    hasher = PasswordHasherService(max_concurrency=1, queue_timeout_seconds=10)
    assert await hasher.verify(row.password_hash, TEST_PASSPHRASE) is True

    [email] = isolated_client.outbox.to(EMAIL)
    assert "/verify-email#token=" in email.body
    assert "?token=" not in email.body
    assert TEST_PASSPHRASE not in email.body


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte, puis tente de le recréer, dans une transaction annulée à la fin.
# ensures  : une adresse déjà inscrite et vérifiée donne exactement la même réponse ; seul son
#            propriétaire est prévenu, par e-mail, et aucun lien n'est émis.
async def test_existing_address_gets_identical_response(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    await create_account(isolated_client)
    new_status, new_body, _ = await register(
        isolated_client, {"email": "bob@exemple.org", "password": TEST_PASSPHRASE}
    )

    status, body, _ = await register(
        isolated_client, {"email": EMAIL, "password": "une toute autre phrase 77"}
    )

    assert (status, body) == (new_status, new_body) == (HTTPStatus.ACCEPTED, ACCEPTED_BODY)
    assert await count_users(db_session) == 2
    notice = isolated_client.outbox.to(EMAIL)[-1]
    assert "possède déjà un" in notice.body
    assert "#token=" not in notice.body


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : inscrit deux fois la même adresse non vérifiée, dans une transaction annulée.
# ensures  : une adresse inscrite mais jamais vérifiée reçoit un nouveau lien, et l'ancien ne
#            fonctionne plus.
async def test_unverified_address_gets_a_new_link(isolated_client: OutboxClient) -> None:
    await register(isolated_client)
    first = isolated_client.outbox.latest_token(EMAIL, "/verify-email")

    await register(isolated_client)
    second = isolated_client.outbox.latest_token(EMAIL, "/verify-email")

    assert first != second
    old = await isolated_client.post(VERIFY_EMAIL_URL, json={"token": first})
    assert old.status_code == HTTPStatus.BAD_REQUEST
    new = await isolated_client.post(VERIFY_EMAIL_URL, json={"token": second})
    assert new.status_code == HTTPStatus.NO_CONTENT


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
    isolated_client: OutboxClient, db_session: AsyncSession, extra_field: dict[str, object]
) -> None:
    status, _, _ = await register(
        isolated_client, {"email": EMAIL, "password": TEST_PASSPHRASE, **extra_field}
    )

    assert status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert await count_users(db_session) == 0
    assert isolated_client.outbox.sent == []


# requires : `email` est une adresse invalide.
# assigns  : rien (la demande est refusée avant toute écriture).
# ensures  : l'adresse est refusée, sans être recopiée dans la réponse.
@pytest.mark.parametrize(
    "email",
    ["pas-une-adresse", "alice@localhost", "ali\u0000ce@exemple.org", "a" * 300 + "@x.fr", 42],
)
async def test_invalid_email_is_refused(isolated_client: OutboxClient, email: object) -> None:
    status, _, raw = await register(isolated_client, {"email": email, "password": TEST_PASSPHRASE})

    assert status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "pas-une-adresse" not in raw


# requires : `password` enfreint la politique de mot de passe.
# assigns  : rien (la demande est refusée avant tout hachage).
# ensures  : le mot de passe est refusé, sans être recopié ; la politique ne dépend pas de
#            l'existence de l'adresse, donc ce refus ne révèle rien.
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
    isolated_client: OutboxClient, db_session: AsyncSession, password: str
) -> None:
    status, _, raw = await register(isolated_client, {"email": EMAIL, "password": password})

    assert status == HTTPStatus.UNPROCESSABLE_ENTITY
    assert password not in raw
    assert await count_users(db_session) == 0


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte dans une transaction annulée à la fin du test.
# ensures  : ni le mot de passe, ni son empreinte, ni le lien n'apparaissent dans les journaux.
async def test_password_hash_and_link_are_never_logged(
    isolated_client: OutboxClient, read_logs: LogReader
) -> None:
    await register(isolated_client)
    token = isolated_client.outbox.latest_token(EMAIL, "/verify-email")

    logs = str(read_logs())
    for secret in (TEST_PASSPHRASE, "$argon2", token, EMAIL):
        assert secret not in logs


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : remplace le hachage par une saturation simulée le temps du test.
# ensures  : un échec du travail d'arrière-plan ne change pas la réponse (toujours 202), ne crée
#            rien, et reste visible dans les journaux, par son seul type.
async def test_background_failure_is_logged_not_exposed(
    database_app: FastAPI,
    isolated_client: OutboxClient,
    db_session: AsyncSession,
    read_logs: LogReader,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : lève toujours l'erreur de saturation.
    async def saturated(_plain: str) -> str:
        raise PasswordHashingBusyError

    monkeypatch.setattr(database_app.state.password_hasher, "hash", saturated)

    status, body, _ = await register(isolated_client)

    assert (status, body) == (HTTPStatus.ACCEPTED, ACCEPTED_BODY)
    assert await count_users(db_session) == 0
    failures = [e for e in read_logs() if e["message"] == "background_task_failed"]
    assert failures == [
        {**failures[0], "task": "registration", "error_type": "PasswordHashingBusyError"}
    ]


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : remplace le hachage par une empreinte non argon2id le temps du test.
# ensures  : si le hachage était un jour défaillant, la base refuse l'empreinte : aucun compte.
async def test_database_refuses_non_argon2id_hash(
    database_app: FastAPI,
    isolated_client: OutboxClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : renvoie une empreinte bcrypt, interdite par la contrainte de la table.
    async def weak_hash(_plain: str) -> str:
        return "$2b$12$" + "x" * 53

    monkeypatch.setattr(database_app.state.password_hasher, "hash", weak_hash)

    status, _, _ = await register(isolated_client)

    assert status == HTTPStatus.ACCEPTED
    assert await count_users(db_session) == 0
    assert isolated_client.outbox.sent == []
