"""Tests de l'envoi réel par SMTP (Mailpit) et de la configuration des e-mails."""

import uuid

import httpx2
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.email import OutgoingEmail, SmtpEmailSender
from tests.conftest import (
    UNUSED_CREDENTIAL,
    DatabaseTestSettings,
    unreachable_database_url,
    unreachable_redis_url,
)

pytestmark = pytest.mark.anyio


# requires : `database_settings` fournit le port SMTP de Mailpit.
# assigns  : rien.
# ensures  : renvoie une configuration de test qui envoie vers Mailpit, sans chiffrement.
def mailpit_settings(database_settings: DatabaseTestSettings) -> Settings:
    return Settings(
        environment="test",
        database_url=unreachable_database_url(),
        smtp_host="127.0.0.1",
        smtp_port=database_settings.smtp_port,
        smtp_security="none",
        # Mailpit n'attend aucune authentification.
        smtp_username=None,
        smtp_password=None,
        _env_file=None,
    )


# requires : Mailpit est démarré.
# assigns  : envoie un e-mail réel à Mailpit.
# ensures  : l'e-mail arrive, avec l'expéditeur, le destinataire, le sujet et le corps attendus.
async def test_email_is_delivered_through_smtp(database_settings: DatabaseTestSettings) -> None:
    recipient = f"livraison-{uuid.uuid4().hex[:12]}@exemple.org"
    sender = SmtpEmailSender(mailpit_settings(database_settings))

    await sender.send(OutgoingEmail(recipient=recipient, subject="Essai", body="Corps de test"))

    async with httpx2.AsyncClient(base_url=database_settings.mailpit_api_url) as mailpit:
        search = await mailpit.get("/api/v1/search", params={"query": f"to:{recipient}"})
        [summary] = search.json()["messages"]
        message = (await mailpit.get(f"/api/v1/message/{summary['ID']}")).json()
    assert message["From"]["Address"] == "ne-pas-repondre@taskhandler.fr"
    assert [to["Address"] for to in message["To"]] == [recipient]
    assert message["Subject"] == "Essai"
    assert message["Text"].strip() == "Corps de test"


# requires : `recipient` contient un retour à la ligne, comme une tentative d'injection d'en-tête.
# assigns  : rien (l'envoi est refusé avant toute connexion).
# ensures  : un en-tête contenant un saut de ligne est refusé : impossible d'ajouter un `Bcc:`.
@pytest.mark.parametrize("recipient", ["a@exemple.org\nBcc: espion@exemple.org", "a@x.fr\r\nX: y"])
async def test_header_injection_is_refused(
    database_settings: DatabaseTestSettings, recipient: str
) -> None:
    sender = SmtpEmailSender(mailpit_settings(database_settings))

    with pytest.raises(ValueError):
        await sender.send(OutgoingEmail(recipient=recipient, subject="Essai", body="x"))


# requires : `overrides` décrit un réglage d'e-mail dangereux en production.
# assigns  : rien.
# ensures  : en production, SMTP non chiffré, non authentifié, ou des liens en HTTP sont refusés.
@pytest.mark.parametrize(
    "overrides",
    [
        {"smtp_security": "none"},
        {"smtp_username": None},
        {"smtp_password": None},
        {"frontend_base_url": "http://app.taskhandler.fr"},
    ],
)
def test_production_requires_secure_email(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "smtp_security": "tls",
        "smtp_username": "taskhandler",
        "smtp_password": SecretStr(UNUSED_CREDENTIAL),
        "frontend_base_url": "https://app.taskhandler.fr",
        **overrides,
    }

    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "database_url": unreachable_database_url("?ssl=verify-full"),
                "redis_url": unreachable_redis_url(),
                **values,
            }
        )


# requires : `field` et `value` décrivent un réglage d'e-mail invalide partout.
# assigns  : rien.
# ensures  : une adresse d'expédition invalide, ou un frontend avec requête ou fragment, sont
#            refusés dès le démarrage.
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email_from", "pas-une-adresse"),
        ("frontend_base_url", "https://app.taskhandler.fr/?x=1"),
        ("frontend_base_url", "https://app.taskhandler.fr/#ancre"),
        ("smtp_port", 0),
    ],
)
def test_invalid_email_settings_are_refused(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {"environment": "test", "database_url": unreachable_database_url(), field: value}
        )
