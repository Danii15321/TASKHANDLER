"""Routes d'authentification.

`router` regroupe les routes publiques listées dans le README. `session_router` exige un jeton
d'accès pour **toutes** ses routes, déclaré une seule fois au niveau du routeur : une route
ajoutée plus tard ne peut pas oublier sa protection (OWASP API5).
"""

from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import APIRouter, BackgroundTasks, HTTPException, Response

from app.core.client_ip import ClientIpDependency
from app.core.dependencies import SettingsDependency
from app.core.email import EmailSenderDependency
from app.core.errors import ProblemResponse, build_problem
from app.core.passwords import PasswordHasherDependency, PasswordHashingBusyError
from app.core.rate_limit import (
    RateLimiterDependency,
    RateLimitExceededError,
    RateLimitUnavailableError,
)
from app.core.tokens import ACCESS_TOKEN_TTL
from app.db.session import SessionDependency, SessionFactoryDependency
from app.modules.auth.account_flows import (
    InvalidOneTimeTokenError,
    PasswordRejectedError,
    guard_password_reset_request,
    process_password_reset_request,
    process_registration,
    process_verification_resend,
    reset_password,
    verify_email,
)
from app.modules.auth.dependencies import AUTHENTICATED_DEPENDENCIES, CurrentUserDependency
from app.modules.auth.limits import protection_unavailable, too_many_requests
from app.modules.auth.schemas import (
    AcceptedResponse,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.modules.auth.service import (
    EmailNotVerifiedError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    IssuedTokens,
    close_all_sessions,
    close_session,
    guard_registration,
    log_in,
    rotate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
session_router = APIRouter(prefix="/auth", tags=["auth"], dependencies=AUTHENTICATED_DEPENDENCIES)

# Délai suggéré au client lorsque le serveur est saturé de hachages.
_BUSY_RETRY_AFTER_SECONDS = "5"
# Message unique : il ne dit jamais si c'est l'adresse ou le mot de passe qui est faux.
INVALID_CREDENTIALS_DETAIL = "Identifiants invalides."
EMAIL_NOT_VERIFIED_DETAIL = (
    "Adresse e-mail non vérifiée. Un lien de vérification vous a été envoyé."
)
INVALID_LINK_DETAIL = "Lien invalide ou expiré."


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


# requires : `error` vient du limiteur ou de Redis.
# assigns  : rien.
# ensures  : renvoie l'erreur HTTP correspondante : 429 avec `Retry-After`, ou 503.
def _limit_error(error: RateLimitExceededError | RateLimitUnavailableError) -> HTTPException:
    if isinstance(error, RateLimitExceededError):
        return too_many_requests(error)
    return protection_unavailable()


# requires : rien (route publique, sans authentification).
# assigns  : programme, après la réponse, la création du compte et l'envoi d'un e-mail.
# ensures  : renvoie 202 à l'identique, que l'adresse soit libre ou déjà inscrite ; 422 si la
#            demande est invalide (indépendamment de l'adresse) ; 429 au-delà des limites ; 503 si
#            Redis est indisponible. Aucune réponse ne contient le mot de passe.
@router.post("/register", status_code=HTTPStatus.ACCEPTED, response_model=AcceptedResponse)
async def register(
    payload: RegisterRequest,
    background_tasks: BackgroundTasks,
    hasher: PasswordHasherDependency,
    limiter: RateLimiterDependency,
    settings: SettingsDependency,
    client_ip: ClientIpDependency,
    sender: EmailSenderDependency,
    session_factory: SessionFactoryDependency,
) -> AcceptedResponse:
    try:
        await guard_registration(limiter, settings, email=payload.email, client_ip=client_ip)
    except (RateLimitExceededError, RateLimitUnavailableError) as error:
        raise _limit_error(error) from None
    # Tout ce qui dépend de l'existence de l'adresse se passe après la réponse.
    background_tasks.add_task(
        process_registration,
        session_factory,
        hasher,
        sender,
        settings,
        email=payload.email,
        password=payload.password,
    )
    return AcceptedResponse(status="accepted")


# requires : rien (route publique : le jeton reçu par e-mail est lui-même la preuve).
# assigns  : consomme le jeton et marque l'adresse vérifiée.
# ensures  : renvoie 204 ; 400 si le lien est inconnu, expiré, déjà utilisé ou d'un autre usage.
@router.post("/verify-email", status_code=HTTPStatus.NO_CONTENT)
async def confirm_email(payload: VerifyEmailRequest, session: SessionDependency) -> None:
    try:
        await verify_email(session, token=payload.token)
    except InvalidOneTimeTokenError:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=INVALID_LINK_DETAIL
        ) from None


# requires : rien (route publique, sans authentification).
# assigns  : ouvre une session ; met à niveau l'empreinte si nécessaire.
# ensures  : renvoie 200 et une paire de jetons ; 401 avec un message unique si l'adresse est
#            inconnue, le mot de passe faux ou le compte inactif ; 403 si, mot de passe correct,
#            l'adresse n'est pas vérifiée (un nouveau lien part alors) ; 429 au-delà des limites
#            ou pendant la temporisation ; 503 si le serveur est saturé ou Redis indisponible.
@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    session: SessionDependency,
    hasher: PasswordHasherDependency,
    limiter: RateLimiterDependency,
    settings: SettingsDependency,
    client_ip: ClientIpDependency,
    background_tasks: BackgroundTasks,
    sender: EmailSenderDependency,
    session_factory: SessionFactoryDependency,
) -> TokenResponse | ProblemResponse:
    try:
        tokens = await log_in(
            session,
            hasher,
            limiter,
            settings,
            email=payload.email,
            password=payload.password,
            client_ip=client_ip,
            now=datetime.now(UTC),
        )
    except (RateLimitExceededError, RateLimitUnavailableError) as error:
        raise _limit_error(error) from None
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except EmailNotVerifiedError as error:
        background_tasks.add_task(
            process_verification_resend,
            session_factory,
            sender,
            limiter,
            settings,
            user_id=error.user_id,
        )
        # Réponse renvoyée, et non levée : FastAPI abandonnerait sinon la tâche programmée.
        problem = build_problem(HTTPStatus.FORBIDDEN, EMAIL_NOT_VERIFIED_DETAIL)
        problem.background = background_tasks
        return problem
    except PasswordHashingBusyError:
        raise _busy() from None
    return _token_response(tokens, response)


# requires : rien (route publique : le jeton de renouvellement est lui-même la preuve).
# assigns  : remplace le jeton utilisé par un nouveau ; coupe la session en cas de réutilisation.
# ensures  : renvoie 200 et une nouvelle paire de jetons, 401 sans préciser la cause, 429 au-delà
#            de la limite, ou 503 si Redis est indisponible.
@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
    limiter: RateLimiterDependency,
) -> TokenResponse:
    try:
        tokens = await rotate_refresh_token(
            session, settings, limiter, refresh_token=payload.refresh_token, now=datetime.now(UTC)
        )
    except (RateLimitExceededError, RateLimitUnavailableError) as error:
        raise _limit_error(error) from None
    except InvalidRefreshTokenError:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED,
            detail="Session invalide ou expirée.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    return _token_response(tokens, response)


# requires : rien (route publique, sans authentification).
# assigns  : programme, après la réponse, l'envoi d'un lien si un compte actif existe.
# ensures  : renvoie 202 à l'identique, adresse connue ou non ; 429 au-delà des limites ; 503 si
#            Redis est indisponible.
@router.post("/password/forgot", status_code=HTTPStatus.ACCEPTED, response_model=AcceptedResponse)
async def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    limiter: RateLimiterDependency,
    settings: SettingsDependency,
    client_ip: ClientIpDependency,
    sender: EmailSenderDependency,
    session_factory: SessionFactoryDependency,
) -> AcceptedResponse:
    try:
        await guard_password_reset_request(
            limiter, settings, email=payload.email, client_ip=client_ip
        )
    except (RateLimitExceededError, RateLimitUnavailableError) as error:
        raise _limit_error(error) from None
    background_tasks.add_task(
        process_password_reset_request, session_factory, sender, settings, email=payload.email
    )
    return AcceptedResponse(status="accepted")


# requires : rien (route publique : le jeton reçu par e-mail est lui-même la preuve).
# assigns  : remplace le mot de passe et coupe toutes les sessions du compte.
# ensures  : renvoie 204 ; 400 si le lien n'est pas valable ; 422 si le mot de passe est refusé
#            (le lien reste alors utilisable) ; 503 si le serveur est saturé.
@router.post("/password/reset", status_code=HTTPStatus.NO_CONTENT)
async def reset_forgotten_password(
    payload: ResetPasswordRequest, session: SessionDependency, hasher: PasswordHasherDependency
) -> None:
    try:
        await reset_password(
            session, hasher, token=payload.token, new_password=payload.new_password
        )
    except InvalidOneTimeTokenError:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST, detail=INVALID_LINK_DETAIL
        ) from None
    except PasswordRejectedError:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Le mot de passe ne doit pas reprendre l'adresse e-mail.",
        ) from None
    except PasswordHashingBusyError:
        raise _busy() from None


# requires : un jeton d'accès valide et la limite par utilisateur (vérifiés par le routeur).
# assigns  : révoque la session du jeton utilisé.
# ensures  : renvoie 204 ; les jetons de cette session sont désormais refusés.
@session_router.post("/logout", status_code=HTTPStatus.NO_CONTENT)
async def logout(current: CurrentUserDependency, session: SessionDependency) -> None:
    await close_session(session, user_id=current.user.id, session_id=current.session_id)


# requires : un jeton d'accès valide et la limite par utilisateur (vérifiés par le routeur).
# assigns  : révoque toutes les sessions du compte et incrémente `token_version`.
# ensures  : renvoie 204 ; plus aucun jeton émis jusqu'ici pour ce compte n'est accepté.
@session_router.post("/logout-all", status_code=HTTPStatus.NO_CONTENT)
async def logout_all(current: CurrentUserDependency, session: SessionDependency) -> None:
    await close_all_sessions(session, user_id=current.user.id)
