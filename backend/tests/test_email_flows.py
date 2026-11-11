"""Tests des parcours par e-mail : vérification d'adresse, oubli et réinitialisation."""

from http import HTTPStatus

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import (
    FORGOT_PASSWORD_URL,
    LOGIN_URL,
    LOGOUT_URL,
    REFRESH_URL,
    REGISTER_URL,
    RESET_PASSWORD_URL,
    TEST_PASSPHRASE,
    VERIFY_EMAIL_URL,
    LogReader,
    OutboxClient,
    bearer,
    create_account,
    login_tokens,
)

pytestmark = pytest.mark.anyio

EMAIL = "alice@exemple.org"
NEW_PASSPHRASE = "orage sur la colline 1987"
INVALID_LINK = "Lien invalide ou expiré."


# requires : `client` est relié à l'application de test.
# assigns  : envoie une tentative de connexion.
# ensures  : renvoie le code HTTP de la réponse.
async def login_status(client: OutboxClient, password: str = TEST_PASSPHRASE) -> int:
    response = await client.post(LOGIN_URL, json={"email": EMAIL, "password": password})
    return response.status_code


# requires : `client` est relié à l'application de test.
# assigns  : demande un lien de réinitialisation pour `email`.
# ensures  : renvoie le code HTTP et le corps de la réponse.
async def forgot(client: OutboxClient, email: str = EMAIL) -> tuple[int, object]:
    response = await client.post(FORGOT_PASSWORD_URL, json={"email": email})
    return response.status_code, response.json()


# requires : `client` est relié à l'application de test.
# assigns  : tente une réinitialisation.
# ensures  : renvoie la réponse HTTP brute.
async def reset(client: OutboxClient, token: str, password: str = NEW_PASSPHRASE) -> int:
    response = await client.post(
        RESET_PASSWORD_URL, json={"token": token, "new_password": password}
    )
    return response.status_code


# requires : `update` modifie les dates du jeton dans la transaction du test.
# assigns  : exécute `update`.
# ensures  : rien d'autre.
async def age_tokens(session: AsyncSession, update: str) -> None:
    await session.execute(text(update))


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : inscrit un compte puis vérifie son adresse, dans une transaction annulée.
# ensures  : avant vérification, la connexion répond 403 et renvoie un lien ; après, elle réussit,
#            et le lien ne sert qu'une fois.
async def test_login_requires_verified_email(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    await isolated_client.post(REGISTER_URL, json={"email": EMAIL, "password": TEST_PASSPHRASE})

    response = await isolated_client.post(
        LOGIN_URL, json={"email": EMAIL, "password": TEST_PASSPHRASE}
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert response.headers["content-type"] == "application/problem+json"
    assert "non vérifiée" in response.json()["detail"]
    assert len(isolated_client.outbox.to(EMAIL)) == 2
    token = isolated_client.outbox.latest_token(EMAIL, "/verify-email")

    assert (await isolated_client.post(VERIFY_EMAIL_URL, json={"token": token})).status_code == (
        HTTPStatus.NO_CONTENT
    )
    assert await login_status(isolated_client) == HTTPStatus.OK
    reused = await isolated_client.post(VERIFY_EMAIL_URL, json={"token": token})
    assert reused.status_code == HTTPStatus.BAD_REQUEST
    assert reused.json()["detail"] == INVALID_LINK


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : inscrit un compte non vérifié, dans une transaction annulée à la fin du test.
# ensures  : un mauvais mot de passe sur un compte non vérifié donne le 401 habituel : seul le
#            détenteur du bon mot de passe apprend que l'adresse attend sa vérification.
async def test_unverified_account_with_wrong_password_reveals_nothing(
    isolated_client: OutboxClient,
) -> None:
    await isolated_client.post(REGISTER_URL, json={"email": EMAIL, "password": TEST_PASSPHRASE})

    assert await login_status(isolated_client, "mauvais mot de passe 2026") == (
        HTTPStatus.UNAUTHORIZED
    )
    assert len(isolated_client.outbox.to(EMAIL)) == 1


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : enchaîne des connexions sur un compte non vérifié, dans une transaction annulée.
# ensures  : le renvoi du lien est limité à 3 par heure : impossible d'inonder une boîte mail.
async def test_verification_resend_is_limited(isolated_client: OutboxClient) -> None:
    await isolated_client.post(REGISTER_URL, json={"email": EMAIL, "password": TEST_PASSPHRASE})

    for _ in range(5):
        assert await login_status(isolated_client) == HTTPStatus.FORBIDDEN

    assert len(isolated_client.outbox.to(EMAIL)) == 1 + 3


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : vieillit un jeton dans une transaction annulée à la fin du test.
# ensures  : un lien de vérification expiré est refusé.
async def test_expired_verification_link_is_refused(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    await isolated_client.post(REGISTER_URL, json={"email": EMAIL, "password": TEST_PASSPHRASE})
    token = isolated_client.outbox.latest_token(EMAIL, "/verify-email")
    await age_tokens(
        db_session,
        "UPDATE one_time_tokens SET created_at = now() - interval '2 days', "
        "expires_at = now() - interval '1 second'",
    )

    response = await isolated_client.post(VERIFY_EMAIL_URL, json={"token": token})

    assert response.status_code == HTTPStatus.BAD_REQUEST


# requires : `payload` est un corps de vérification invalide.
# assigns  : rien.
# ensures  : un jeton mal formé est refusé en 422, avant toute requête en base.
@pytest.mark.parametrize(
    "payload", [{}, {"token": "court"}, {"token": "!" * 43}, {"token": "a" * 43, "user_id": "x"}]
)
async def test_malformed_verification_is_refused(
    isolated_client: OutboxClient, payload: dict[str, object]
) -> None:
    response = await isolated_client.post(VERIFY_EMAIL_URL, json=payload)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : demande des liens pour une adresse connue, une inconnue et un compte inactif.
# ensures  : les trois réponses sont identiques ; seul le compte actif reçoit un e-mail.
async def test_forgot_password_is_uniform(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    await create_account(isolated_client)
    await create_account(isolated_client, email="inactif@exemple.org")
    await db_session.execute(
        text("UPDATE users SET is_active = false WHERE email = 'inactif@exemple.org'")
    )
    sent_before = len(isolated_client.outbox.sent)

    responses = [
        await forgot(isolated_client),
        await forgot(isolated_client, "inconnu@exemple.org"),
        await forgot(isolated_client, "inactif@exemple.org"),
    ]

    assert responses == [(HTTPStatus.ACCEPTED, {"status": "accepted"})] * 3
    new_emails = isolated_client.outbox.sent[sent_before:]
    assert [email.recipient for email in new_emails] == [EMAIL]
    assert "/reset-password#token=" in new_emails[0].body


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : ouvre deux sessions, puis réinitialise le mot de passe, dans une transaction annulée.
# ensures  : le nouveau mot de passe fonctionne, l'ancien non ; toutes les sessions sont coupées,
#            jetons d'accès compris ; le lien ne sert qu'une fois.
async def test_reset_password_revokes_every_session(
    isolated_client: OutboxClient, db_session: AsyncSession, read_logs: LogReader
) -> None:
    await create_account(isolated_client)
    laptop = await login_tokens(isolated_client)
    await forgot(isolated_client)
    token = isolated_client.outbox.latest_token(EMAIL, "/reset-password")

    assert await reset(isolated_client, token) == HTTPStatus.NO_CONTENT

    assert await login_status(isolated_client) == HTTPStatus.UNAUTHORIZED
    assert await login_status(isolated_client, NEW_PASSPHRASE) == HTTPStatus.OK
    stale_access = await isolated_client.post(LOGOUT_URL, headers=bearer(laptop["access_token"]))
    assert stale_access.status_code == HTTPStatus.UNAUTHORIZED
    stale_refresh = await isolated_client.post(
        REFRESH_URL, json={"refresh_token": laptop["refresh_token"]}
    )
    assert stale_refresh.status_code == HTTPStatus.UNAUTHORIZED
    assert await reset(isolated_client, token, "encore une autre phrase 42") == (
        HTTPStatus.BAD_REQUEST
    )
    reasons = (
        (
            await db_session.execute(
                text(
                    "SELECT DISTINCT revocation_reason FROM refresh_tokens "
                    "WHERE revoked_at IS NOT NULL"
                )
            )
        )
        .scalars()
        .all()
    )
    assert reasons == ["password_reset"]
    assert any(event["message"] == "password_reset" for event in read_logs())
    assert token not in str(read_logs())


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : demande deux liens successifs, dans une transaction annulée à la fin du test.
# ensures  : seul le dernier lien envoyé fonctionne.
async def test_new_reset_link_invalidates_previous_one(isolated_client: OutboxClient) -> None:
    await create_account(isolated_client)
    await forgot(isolated_client)
    first = isolated_client.outbox.latest_token(EMAIL, "/reset-password")
    await forgot(isolated_client)
    second = isolated_client.outbox.latest_token(EMAIL, "/reset-password")

    assert await reset(isolated_client, first) == HTTPStatus.BAD_REQUEST
    assert await reset(isolated_client, second) == HTTPStatus.NO_CONTENT


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : vieillit un jeton dans une transaction annulée à la fin du test.
# ensures  : un lien de réinitialisation de plus de 30 minutes est refusé.
async def test_expired_reset_link_is_refused(
    isolated_client: OutboxClient, db_session: AsyncSession
) -> None:
    await create_account(isolated_client)
    await forgot(isolated_client)
    token = isolated_client.outbox.latest_token(EMAIL, "/reset-password")
    await age_tokens(
        db_session,
        "UPDATE one_time_tokens SET created_at = now() - interval '31 minutes', "
        "expires_at = now() - interval '1 minute' WHERE purpose = 'password_reset'",
    )

    assert await reset(isolated_client, token) == HTTPStatus.BAD_REQUEST


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte, dans une transaction annulée à la fin du test.
# ensures  : un lien de vérification ne peut pas réinitialiser un mot de passe.
async def test_link_cannot_be_used_for_another_purpose(isolated_client: OutboxClient) -> None:
    await isolated_client.post(REGISTER_URL, json={"email": EMAIL, "password": TEST_PASSPHRASE})
    verification = isolated_client.outbox.latest_token(EMAIL, "/verify-email")

    assert await reset(isolated_client, verification) == HTTPStatus.BAD_REQUEST


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : tente une réinitialisation refusée, puis la refait, dans une transaction annulée.
# ensures  : un mot de passe reprenant l'adresse est refusé en 422 sans consommer le lien, qui
#            reste utilisable avec un meilleur mot de passe ; un mot de passe trop faible est
#            refusé avant même de toucher au lien.
async def test_rejected_password_keeps_link_usable(isolated_client: OutboxClient) -> None:
    await create_account(isolated_client)
    await forgot(isolated_client)
    token = isolated_client.outbox.latest_token(EMAIL, "/reset-password")

    assert await reset(isolated_client, token, "alice-violet-2026") == (
        HTTPStatus.UNPROCESSABLE_ENTITY
    )
    assert await reset(isolated_client, token, "asdfasdfasdf") == HTTPStatus.UNPROCESSABLE_ENTITY
    assert await reset(isolated_client, token) == HTTPStatus.NO_CONTENT


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : inscrit un compte puis le réinitialise sans l'avoir vérifié.
# ensures  : recevoir le lien de réinitialisation prouve la possession de l'adresse : elle devient
#            vérifiée, et la connexion fonctionne.
async def test_reset_verifies_the_address(isolated_client: OutboxClient) -> None:
    await isolated_client.post(REGISTER_URL, json={"email": EMAIL, "password": TEST_PASSPHRASE})
    await forgot(isolated_client)
    token = isolated_client.outbox.latest_token(EMAIL, "/reset-password")

    assert await reset(isolated_client, token) == HTTPStatus.NO_CONTENT
    assert await login_status(isolated_client, NEW_PASSPHRASE) == HTTPStatus.OK


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : envoie plusieurs demandes pour une même adresse.
# ensures  : au-delà de 3 demandes par heure et par adresse, la demande est refusée en 429 :
#            impossible d'inonder la boîte d'une victime.
async def test_forgot_password_is_limited_per_email(isolated_client: OutboxClient) -> None:
    statuses = [(await forgot(isolated_client, "cible@exemple.org"))[0] for _ in range(4)]

    assert statuses == [HTTPStatus.ACCEPTED] * 3 + [HTTPStatus.TOO_MANY_REQUESTS]
