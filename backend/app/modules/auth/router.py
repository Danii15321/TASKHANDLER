"""Routes d'authentification.

`router` regroupe les routes publiques listées dans le README. `session_router` exige un jeton
d'accès pour **toutes** ses routes, déclaré une seule fois au niveau du routeur : une route
ajoutée plus tard ne peut pas oublier sa protection (OWASP API5).
"""

from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core.dependencies import SettingsDependency
from app.core.passwords import PasswordHasherDependency, PasswordHashingBusyError
from app.core.tokens import ACCESS_TOKEN_TTL
from app.db.session import SessionDependency
from app.modules.auth.dependencies import CurrentUserDependency, get_current_user
from app.modules.auth.schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse
from app.modules.auth.service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    IssuedTokens,
    authenticate,
    close_all_sessions,
    close_session,
    open_session,
    register_user,
    rotate_refresh_token,
)
from app.modules.users.schemas import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])
session_router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(get_current_user)])

# Délai suggéré au client lorsque le serveur est saturé de hachages.
_BUSY_RETRY_AFTER_SECONDS = "5"
# Message unique : il ne dit jamais si c'est l'adresse ou le mot de passe qui est faux.
INVALID_CREDENTIALS_DETAIL = "Identifiants invalides."


# requires : rien.
# assigns  : rien.
# ensures  : renvoie l'erreur 503 d'un serveur saturé de hachages.
def _busy() -> HTTPException:
    return HTTPException(
        status_code=HTTPStatus.SERVICE_UNAVAILABLE,
        headers={"Retry-After": _BUSY_RETRY_AFTER_SECONDS},
    )


# requires : `tokens` vient d'être émis.
# assigns  : ajoute `Cache-Control: no-store` à la réponse.
# ensures  : renvoie la réponse jetons ; aucun cache (navigateur, proxy) ne doit la conserver.
def _token_response(tokens: IssuedTokens, response: Response) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_type="bearer",  # noqa: S106 — type de jeton OAuth (RFC 6750), pas un secret
        expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
    )


# requires : rien (route publique, sans authentification).
# assigns  : crée un compte dans `users`.
# ensures  : renvoie 201 et la vue publique du compte ; 409 si l'adresse existe déjà ; 422 si la
#            demande est invalide ; 503 si le serveur est saturé. Aucune réponse ne contient le
#            mot de passe ni son empreinte.
@router.post("/register", status_code=HTTPStatus.CREATED, response_model=UserRead)
async def register(
    payload: RegisterRequest, session: SessionDependency, hasher: PasswordHasherDependency
) -> UserRead:
    try:
        user = await register_user(session, hasher, email=payload.email, password=payload.password)
    except EmailAlreadyRegisteredError:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT, detail="Cette adresse ne peut pas être utilisée."
        ) from None
    except PasswordHashingBusyError:
        raise _busy() from None
    # Conversion explicite : le modèle ORM ne quitte jamais la couche service.
    return UserRead.model_validate(user)


# requires : rien (route publique, sans authentification).
# assigns  : ouvre une session ; met à niveau l'empreinte si nécessaire.
# ensures  : renvoie 200 et une paire de jetons ; 401 avec un message unique si l'adresse est
#            inconnue, le mot de passe faux ou le compte inactif ; 503 si le serveur est saturé.
@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: SessionDependency,
    hasher: PasswordHasherDependency,
    settings: SettingsDependency,
) -> TokenResponse:
    try:
        user = await authenticate(session, hasher, email=payload.email, password=payload.password)
        tokens = await open_session(session, settings, user, now=datetime.now(UTC))
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except PasswordHashingBusyError:
        raise _busy() from None
    return _token_response(tokens, response)


# requires : rien (route publique : le jeton de renouvellement est lui-même la preuve).
# assigns  : remplace le jeton utilisé par un nouveau ; coupe la session en cas de réutilisation.
# ensures  : renvoie 200 et une nouvelle paire de jetons, ou 401 sans préciser la cause.
@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
) -> TokenResponse:
    try:
        tokens = await rotate_refresh_token(
            session, settings, refresh_token=payload.refresh_token, now=datetime.now(UTC)
        )
    except InvalidRefreshTokenError:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Session invalide ou expirée.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return _token_response(tokens, response)


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : révoque la session du jeton utilisé.
# ensures  : renvoie 204 ; les jetons de cette session sont désormais refusés.
@session_router.post("/logout", status_code=HTTPStatus.NO_CONTENT)
async def logout(current: CurrentUserDependency, session: SessionDependency) -> None:
    await close_session(session, user_id=current.user.id, session_id=current.session_id)


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : révoque toutes les sessions du compte et incrémente `token_version`.
# ensures  : renvoie 204 ; plus aucun jeton émis jusqu'ici pour ce compte n'est accepté.
@session_router.post("/logout-all", status_code=HTTPStatus.NO_CONTENT)
async def logout_all(current: CurrentUserDependency, session: SessionDependency) -> None:
    await close_all_sessions(session, user_id=current.user.id)
