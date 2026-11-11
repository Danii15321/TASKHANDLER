"""Routes du compte de l'utilisateur connecté (`/users/me`).

Authentification et limite par utilisateur sont déclarées au niveau du routeur. Les actions
irréversibles ou sensibles exigent en plus le mot de passe actuel (réauthentification).
"""

from http import HTTPStatus

from fastapi import APIRouter, HTTPException, Response

from app.core.passwords import PasswordHasherDependency, PasswordHashingBusyError
from app.core.rate_limit import (
    RateLimiterDependency,
    RateLimitExceededError,
    RateLimitUnavailableError,
)
from app.db.session import SessionDependency
from app.modules.auth.dependencies import AUTHENTICATED_DEPENDENCIES, CurrentUserDependency
from app.modules.auth.limits import protection_unavailable, too_many_requests
from app.modules.users import service
from app.modules.users.schemas import (
    AccountDeletionRequest,
    AccountExport,
    PasswordChangeRequest,
    UserRead,
)
from app.modules.users.service import (
    EXPORT_LIMIT,
    PasswordRejectedError,
    ReauthenticationFailedError,
)

router = APIRouter(prefix="/users/me", tags=["users"], dependencies=AUTHENTICATED_DEPENDENCIES)

# 403 et non 401 : le jeton est valide, c'est la preuve supplémentaire qui manque. Un 401 ferait
# croire au client que sa session a expiré.
_REAUTHENTICATION_FAILED = "Mot de passe actuel incorrect."


# requires : `error` vient du limiteur, de Redis, du hachage ou de la réauthentification.
# assigns  : rien.
# ensures  : renvoie l'erreur HTTP correspondante.
def _account_error(error: Exception) -> HTTPException:
    if isinstance(error, RateLimitExceededError):
        return too_many_requests(error)
    if isinstance(error, RateLimitUnavailableError):
        return protection_unavailable()
    if isinstance(error, ReauthenticationFailedError):
        return HTTPException(status_code=HTTPStatus.FORBIDDEN, detail=_REAUTHENTICATION_FAILED)
    if isinstance(error, PasswordRejectedError):
        return HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Le mot de passe ne doit pas reprendre l'adresse e-mail.",
        )
    return HTTPException(status_code=HTTPStatus.SERVICE_UNAVAILABLE, headers={"Retry-After": "5"})


_HANDLED = (
    RateLimitExceededError,
    RateLimitUnavailableError,
    ReauthenticationFailedError,
    PasswordRejectedError,
    PasswordHashingBusyError,
)


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : rien.
# ensures  : renvoie la vue publique du compte, sans empreinte ni donnée interne.
@router.get("", response_model=UserRead)
async def read_me(current: CurrentUserDependency) -> UserRead:
    return UserRead.model_validate(current.user)


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : remplace le mot de passe et coupe toutes les sessions du compte.
# ensures  : renvoie 204 (le client doit se reconnecter) ; 403 si le mot de passe actuel est faux ;
#            422 si le nouveau est refusé ; 429 après 5 essais par minute ; rien n'est modifié en
#            cas d'erreur.
@router.post("/password", status_code=HTTPStatus.NO_CONTENT)
async def change_password(
    payload: PasswordChangeRequest,
    current: CurrentUserDependency,
    session: SessionDependency,
    hasher: PasswordHasherDependency,
    limiter: RateLimiterDependency,
) -> None:
    try:
        await service.change_password(
            session,
            hasher,
            limiter,
            user=current.user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except _HANDLED as error:
        raise _account_error(error) from None


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : supprime le compte et toutes ses données.
# ensures  : renvoie 204 ; 403 si le mot de passe est faux, et le compte reste intact ; 429 après
#            5 essais par minute.
@router.delete("", status_code=HTTPStatus.NO_CONTENT)
async def delete_me(
    payload: AccountDeletionRequest,
    current: CurrentUserDependency,
    session: SessionDependency,
    hasher: PasswordHasherDependency,
    limiter: RateLimiterDependency,
) -> None:
    try:
        await service.delete_account(
            session, hasher, limiter, user=current.user, password=payload.password
        )
    except _HANDLED as error:
        raise _account_error(error) from None


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : enregistre la demande dans le compteur d'export du compte.
# ensures  : renvoie toutes les données du compte en JSON, à télécharger, jamais mises en cache ;
#            429 au-delà de 5 exports par heure.
@router.get("/export", response_model=AccountExport)
async def export_me(
    response: Response,
    current: CurrentUserDependency,
    session: SessionDependency,
    limiter: RateLimiterDependency,
) -> AccountExport:
    try:
        await limiter.hit(EXPORT_LIMIT, str(current.user.id))
    except (RateLimitExceededError, RateLimitUnavailableError) as error:
        raise _account_error(error) from None
    response.headers["Content-Disposition"] = 'attachment; filename="taskhandler-export.json"'
    return await service.export_account(session, user=current.user)
