"""Session du navigateur : cookie du jeton de renouvellement et protection CSRF.

Le frontend appelle l'API directement depuis le navigateur. Le jeton d'accès, court, reste en
mémoire JavaScript ; le jeton de renouvellement, qui ouvre la session pour des jours, ne doit
jamais être lisible par un script. Il voyage donc dans un cookie `HttpOnly`, que le navigateur
envoie seul, et uniquement à la route de renouvellement.

Un cookie envoyé automatiquement ouvre la porte au CSRF : une page tierce pourrait déclencher la
requête à la place de l'utilisateur. Deux contrôles indépendants s'y opposent :

1. un en-tête personnalisé obligatoire (`X-CSRF-Protection`) : un formulaire HTML ne sait pas
   l'envoyer, et `fetch` ne peut l'ajouter vers une autre origine qu'après une requête de
   contrôle CORS, que l'API refuse à toute origine hors liste ;
2. l'en-tête `Origin`, comparé à la liste explicite des origines du frontend : il est posé par le
   navigateur et aucun script ne peut le falsifier.
"""

import logging
import re
from datetime import datetime
from http import HTTPStatus
from typing import Annotated

from fastapi import Cookie, HTTPException, Request, Response

from app.core.dependencies import SettingsDependency
from app.core.tokens import OPAQUE_TOKEN_LENGTH

audit_logger = logging.getLogger("app.audit")

# Le préfixe `__Secure-` oblige le navigateur à refuser ce cookie s'il n'est pas `Secure` : un
# réglage oublié ne peut pas le faire voyager en clair.
REFRESH_COOKIE_NAME = "__Secure-refresh_token"
# Le cookie n'est envoyé qu'à la route de renouvellement : ni les autres routes de l'API, ni
# leurs journaux, ni un éventuel défaut ailleurs ne le voient passer.
REFRESH_COOKIE_PATH = "/api/v1/auth/refresh"

CSRF_HEADER_NAME = "X-CSRF-Protection"
CSRF_HEADER_VALUE = "1"
# Message unique : il ne dit pas lequel des contrôles a échoué.
CSRF_REJECTED_DETAIL = "Requête refusée."

# Valeur du cookie lue par FastAPI ; `None` si le navigateur ne l'a pas envoyé.
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]

# Format exact de `secrets.token_urlsafe(32)`, comme `OpaqueTokenValue` pour un corps JSON.
_OPAQUE_TOKEN = re.compile(rf"[A-Za-z0-9_-]{{{OPAQUE_TOKEN_LENGTH}}}")


# requires : `value` est la valeur brute du cookie, ou `None`.
# assigns  : rien.
# ensures  : renvoie la valeur si elle a exactement le format d'un jeton émis par l'API, `None`
#            sinon. Une valeur mal formée n'atteint jamais la base de données.
def well_formed_refresh_token(value: str | None) -> str | None:
    if value is None or not _OPAQUE_TOKEN.fullmatch(value):
        return None
    return value


# requires : `token` vient d'être émis ; `expires_at` et `now` portent un fuseau horaire.
# assigns  : ajoute un en-tête `Set-Cookie` à `response`.
# ensures  : le navigateur conserve le jeton jusqu'à son expiration, hors de portée de JavaScript
#            (`HttpOnly`), en HTTPS seulement (`Secure`), jamais envoyé depuis un autre site
#            (`SameSite=Strict`) et seulement vers la route de renouvellement.
def set_refresh_cookie(
    response: Response, token: str, *, expires_at: datetime, now: datetime
) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        # Même durée que le jeton en base : un cookie plus long ne servirait qu'à être refusé.
        max_age=max(0, int((expires_at - now).total_seconds())),
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


# requires : rien.
# assigns  : ajoute un en-tête `Set-Cookie` à `response`.
# ensures  : le navigateur efface le cookie de renouvellement. Les attributs reprennent ceux du
#            dépôt : un navigateur n'efface que le cookie de même nom, chemin et domaine.
def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


# requires : rien.
# assigns  : rien.
# ensures  : renvoie la valeur d'un en-tête `Set-Cookie` qui efface le cookie, à joindre à une
#            erreur levée (une `HTTPException` ne porte que des en-têtes, pas de `Response`).
def cleared_refresh_cookie_header() -> str:
    response = Response()
    clear_refresh_cookie(response)
    return response.headers["set-cookie"]


# requires : `request` est la requête en cours, `settings` la configuration validée.
# assigns  : écrit un événement d'audit en cas de refus.
# ensures  : lève `HTTPException(403)` si l'en-tête personnalisé manque, ou si une origine est
#            présente mais absente de la liste ; si `require_origin`, une origine absente est
#            aussi refusée.
def _check_csrf(request: Request, settings: SettingsDependency, *, require_origin: bool) -> None:
    reason: str | None = None
    origin = request.headers.get("origin")
    if request.headers.get(CSRF_HEADER_NAME) != CSRF_HEADER_VALUE:
        reason = "missing_header"
    elif origin is None:
        if require_origin:
            reason = "missing_origin"
    elif origin not in settings.cors_origins:
        reason = "foreign_origin"

    if reason is not None:
        # L'origine reçue n'est pas journalisée : c'est une valeur contrôlée par l'appelant.
        audit_logger.warning("csrf_rejected", extra={"route": request.url.path, "reason": reason})
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=CSRF_REJECTED_DETAIL)


# requires : la requête vise `POST /auth/login`.
# assigns  : écrit un événement d'audit en cas de refus.
# ensures  : se termine si l'en-tête personnalisé est présent et que l'origine, si le client en
#            envoie une, est autorisée ; sinon lève `HTTPException(403)`. Une origine absente
#            reste acceptée : un navigateur en envoie toujours une sur un `POST`, et cette route
#            reste utilisable par un outil d'administration ou de test.
async def require_csrf_for_login(request: Request, settings: SettingsDependency) -> None:
    _check_csrf(request, settings, require_origin=False)


# requires : la requête vise `POST /auth/refresh`.
# assigns  : écrit un événement d'audit en cas de refus.
# ensures  : se termine si l'en-tête personnalisé est présent et que l'origine est présente et
#            autorisée ; sinon lève `HTTPException(403)`. Le cookie n'existe que dans un
#            navigateur, qui envoie toujours `Origin` sur un `POST` : son absence est suspecte.
async def require_csrf_for_refresh(request: Request, settings: SettingsDependency) -> None:
    _check_csrf(request, settings, require_origin=True)
