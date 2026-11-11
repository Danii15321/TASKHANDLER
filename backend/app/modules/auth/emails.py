"""Contenu des e-mails de compte. Texte brut, sans aucune donnée fournie par un utilisateur."""

from app.core.config import Settings
from app.core.email import OutgoingEmail


# requires : `path` commence par `/`, `token` est un jeton opaque URL-safe.
# assigns  : rien (fonction pure).
# ensures  : renvoie le lien du frontend, avec le jeton dans le fragment (`#token=…`). Un fragment
#            n'est jamais envoyé au serveur : le jeton n'apparaît ni dans les journaux d'accès,
#            ni dans l'en-tête `Referer`. Le frontend le lit, puis l'envoie dans un corps `POST`.
def _link(settings: Settings, path: str, token: str) -> str:
    return f"{str(settings.frontend_base_url).rstrip('/')}{path}#token={token}"


# requires : `recipient` est l'adresse du compte, `token` un jeton de vérification neuf.
# assigns  : rien.
# ensures  : renvoie l'e-mail de vérification d'adresse.
def verification_email(settings: Settings, recipient: str, token: str) -> OutgoingEmail:
    hours = settings.email_verification_ttl_hours
    return OutgoingEmail(
        recipient=recipient,
        subject="TaskHandler : confirmez votre adresse e-mail",
        body=(
            "Bonjour,\n\n"
            "Pour activer votre compte TaskHandler, ouvrez ce lien :\n"
            f"{_link(settings, '/verify-email', token)}\n\n"
            f"Il est valable {hours} heures et ne fonctionne qu'une fois.\n"
            "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail.\n"
        ),
    )


# requires : `recipient` est l'adresse d'un compte déjà vérifié.
# assigns  : rien.
# ensures  : renvoie l'avertissement envoyé quand quelqu'un s'inscrit avec une adresse déjà prise.
#            La réponse HTTP reste identique ; seul le propriétaire de l'adresse est prévenu.
def account_exists_email(settings: Settings, recipient: str) -> OutgoingEmail:
    return OutgoingEmail(
        recipient=recipient,
        subject="TaskHandler : tentative d'inscription avec votre adresse",
        body=(
            "Bonjour,\n\n"
            "Quelqu'un a demandé à créer un compte TaskHandler avec votre adresse, qui en possède "
            "déjà un.\n"
            "Si c'était vous, connectez-vous, ou utilisez « mot de passe oublié ».\n"
            "Sinon, vous pouvez ignorer cet e-mail : votre compte n'a pas été modifié.\n"
        ),
    )


# requires : `recipient` est l'adresse du compte, `token` un jeton de réinitialisation neuf.
# assigns  : rien.
# ensures  : renvoie l'e-mail de réinitialisation du mot de passe.
def password_reset_email(settings: Settings, recipient: str, token: str) -> OutgoingEmail:
    minutes = settings.password_reset_ttl_minutes
    return OutgoingEmail(
        recipient=recipient,
        subject="TaskHandler : réinitialisation de votre mot de passe",
        body=(
            "Bonjour,\n\n"
            "Pour choisir un nouveau mot de passe, ouvrez ce lien :\n"
            f"{_link(settings, '/reset-password', token)}\n\n"
            f"Il est valable {minutes} minutes et ne fonctionne qu'une fois. Toutes vos sessions "
            "seront fermées.\n"
            "Si vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail : votre mot de "
            "passe reste inchangé.\n"
        ),
    )
