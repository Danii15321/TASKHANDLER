"""Authentification des requêtes : la dépendance `get_current_user`."""

import uuid
from dataclasses import dataclass
from http import HTTPStatus
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.dependencies import SettingsDependency
from app.core.tokens import InvalidAccessTokenError, decode_access_token
from app.db.session import SessionDependency
from app.modules.auth.repository import get_authenticated_user
from app.modules.users.models import User

# `auto_error=False` : FastAPI répondrait 403 sans notre format d'erreur. On répond nous-mêmes
# 401, la réponse correcte à une authentification absente ou invalide.
_bearer_scheme = HTTPBearer(auto_error=False)

UNAUTHENTICATED_DETAIL = "Authentification requise."


@dataclass(frozen=True)
class CurrentUser:
    """Utilisateur authentifié et session à laquelle appartient son jeton d'accès."""

    user: User
    session_id: uuid.UUID


# requires : rien.
# assigns  : rien.
# ensures  : renvoie l'erreur 401 unique, identique quelle que soit la cause du refus.
def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=HTTPStatus.UNAUTHORIZED,
        detail=UNAUTHENTICATED_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


# requires : la requête porte, ou non, un en-tête `Authorization: Bearer <jeton>`.
# assigns  : rien (lecture seule).
# ensures  : renvoie l'utilisateur, rechargé en base à chaque requête : actif, avec la bonne
#            version de jeton et une session encore ouverte. Sinon lève une erreur 401.
async def get_current_user(
    settings: SettingsDependency,
    session: SessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> CurrentUser:
    if credentials is None:
        raise _unauthenticated()
    try:
        claims = decode_access_token(credentials.credentials, secret=settings.jwt_secret)
    except InvalidAccessTokenError:
        raise _unauthenticated() from None

    # Un jeton valide ne suffit pas : le compte a pu être désactivé, déconnecté partout ou voir
    # sa session coupée depuis l'émission du jeton. La base fait foi, pas le jeton.
    user = await get_authenticated_user(
        session, user_id=claims.sub, session_id=claims.sid, token_version=claims.ver
    )
    if user is None:
        raise _unauthenticated()
    return CurrentUser(user=user, session_id=claims.sid)


CurrentUserDependency = Annotated[CurrentUser, Depends(get_current_user)]
