"""Limites de débit de l'authentification, et réponses HTTP associées."""

import logging
from http import HTTPStatus

from fastapi import HTTPException

from app.core.config import Settings
from app.core.rate_limit import Limit, RateLimitExceededError

logger = logging.getLogger(__name__)

# Temporisation progressive de la connexion, par couple IP + compte.
LOGIN_BACKOFF_SCOPE = "login-backoff"
_MINUTE = 60
_HOUR = 60 * 60
TOO_MANY_REQUESTS_DETAIL = "Trop de tentatives. Réessayez plus tard."


# requires : `settings` est valide.
# assigns  : rien.
# ensures  : renvoie la limite de connexion par IP (20 par minute par défaut).
def login_ip_limit(settings: Settings) -> Limit:
    return Limit("login-ip", settings.login_ip_limit_per_minute, _MINUTE)


# requires : `settings` est valide.
# assigns  : rien.
# ensures  : renvoie la limite de connexion par couple IP + compte (5 par minute par défaut).
def login_account_limit(settings: Settings) -> Limit:
    return Limit("login-account", settings.login_account_limit_per_minute, _MINUTE)


# requires : `settings` est valide.
# assigns  : rien.
# ensures  : renvoie la limite d'inscription par IP (5 par heure par défaut).
def register_ip_limit(settings: Settings) -> Limit:
    return Limit("register-ip", settings.register_ip_limit_per_hour, _HOUR)


# requires : `settings` est valide.
# assigns  : rien.
# ensures  : renvoie la limite d'inscription par adresse (3 par heure par défaut).
def register_email_limit(settings: Settings) -> Limit:
    return Limit("register-email", settings.register_email_limit_per_hour, _HOUR)


# requires : `settings` est valide.
# assigns  : rien.
# ensures  : renvoie la limite de renouvellement par utilisateur (30 par minute par défaut).
def refresh_user_limit(settings: Settings) -> Limit:
    return Limit("refresh-user", settings.refresh_user_limit_per_minute, _MINUTE)


# requires : `settings` est valide.
# assigns  : rien.
# ensures  : renvoie la limite des routes authentifiées par utilisateur (120 par minute).
def authenticated_user_limit(settings: Settings) -> Limit:
    return Limit("authenticated-user", settings.authenticated_user_limit_per_minute, _MINUTE)


# requires : `error` vient du limiteur.
# assigns  : rien.
# ensures  : renvoie l'erreur 429 avec `Retry-After`, sans dire quelle limite a été atteinte.
def too_many_requests(error: RateLimitExceededError) -> HTTPException:
    return HTTPException(
        status_code=HTTPStatus.TOO_MANY_REQUESTS,
        detail=TOO_MANY_REQUESTS_DETAIL,
        headers={"Retry-After": str(error.retry_after_seconds)},
    )


# requires : Redis vient d'échouer sur une route d'authentification.
# assigns  : écrit un avertissement dans les journaux.
# ensures  : renvoie l'erreur 503 générique : sans compteur, l'authentification est refusée
#            plutôt que de laisser passer une attaque par force brute.
def protection_unavailable() -> HTTPException:
    logger.warning("rate_limit_unavailable")
    return HTTPException(status_code=HTTPStatus.SERVICE_UNAVAILABLE)
