"""Envoi des e-mails par SMTP, derrière une interface remplaçable dans les tests."""

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Annotated, Protocol

import anyio.to_thread
from fastapi import Depends, Request

from app.core.config import Settings
from app.core.tls import client_tls_context

# Un serveur SMTP lent ne doit pas immobiliser un fil d'exécution indéfiniment.
_SMTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class OutgoingEmail:
    """E-mail en texte brut : pas de HTML, donc aucun contenu actif à injecter."""

    recipient: str
    subject: str
    # `repr=False` : le corps contient un lien secret, qui ne doit pas apparaître si on l'affiche.
    body: str = field(repr=False)


class EmailSender(Protocol):
    """Tout moyen d'envoyer un e-mail : SMTP en vrai, enregistrement en mémoire dans les tests."""

    # requires : `email.recipient` est une adresse validée.
    # assigns  : envoie l'e-mail.
    # ensures  : se termine une fois l'e-mail remis ; lève une exception en cas d'échec.
    async def send(self, email: OutgoingEmail) -> None: ...


class SmtpEmailSender:
    """Envoi par SMTP ; quand la connexion est chiffrée, le certificat du serveur est vérifié."""

    # requires : `settings` a été validée (chiffrement et authentification hors développement).
    # assigns  : rien (aucune connexion n'est ouverte ici).
    # ensures  : l'expéditeur est prêt.
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # requires : `email.recipient` est une adresse validée.
    # assigns  : ouvre une connexion SMTP, envoie l'e-mail, puis la ferme.
    # ensures  : se termine une fois l'e-mail remis ; l'envoi, bloquant, s'exécute hors de la
    #            boucle d'événements.
    async def send(self, email: OutgoingEmail) -> None:
        await anyio.to_thread.run_sync(self._send_blocking, email)

    # requires : appelée hors de la boucle d'événements.
    # assigns  : ouvre une connexion SMTP, envoie l'e-mail, puis la ferme.
    # ensures  : se termine une fois l'e-mail remis ; lève `smtplib.SMTPException` ou `OSError`
    #            en cas d'échec.
    def _send_blocking(self, email: OutgoingEmail) -> None:
        settings = self._settings
        message = EmailMessage()
        # La politique par défaut d'`EmailMessage` refuse tout retour à la ligne dans un en-tête :
        # aucune injection d'en-tête (`Bcc:` ajouté par un attaquant) n'est possible.
        message["From"] = settings.email_from
        message["To"] = email.recipient
        message["Subject"] = email.subject
        message.set_content(email.body)

        # Certificat et nom d'hôte du serveur SMTP vérifiés, TLS 1.2 au moins.
        context = client_tls_context(settings)
        smtp: smtplib.SMTP
        if settings.smtp_security == "tls":
            smtp = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=_SMTP_TIMEOUT_SECONDS,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=_SMTP_TIMEOUT_SECONDS
            )
        with smtp:
            if settings.smtp_security == "starttls":
                smtp.starttls(context=context)
            if settings.smtp_username and settings.smtp_password is not None:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(message)


# requires : l'application a été assemblée par `create_app`.
# assigns  : rien.
# ensures  : renvoie l'expéditeur d'e-mails de l'application.
def get_email_sender(request: Request) -> EmailSender:
    sender: EmailSender = request.app.state.email_sender
    return sender


EmailSenderDependency = Annotated[EmailSender, Depends(get_email_sender)]
