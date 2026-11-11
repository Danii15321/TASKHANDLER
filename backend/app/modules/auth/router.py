"""Routes d'authentification. `register` est publique, comme indiqué dans le README."""

from http import HTTPStatus

from fastapi import APIRouter, HTTPException

from app.core.passwords import PasswordHasherDependency, PasswordHashingBusyError
from app.db.session import SessionDependency
from app.modules.auth.schemas import RegisterRequest
from app.modules.auth.service import EmailAlreadyRegisteredError, register_user
from app.modules.users.schemas import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

# Délai suggéré au client lorsque le serveur est saturé de hachages.
_BUSY_RETRY_AFTER_SECONDS = "5"


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
        raise HTTPException(
            status_code=HTTPStatus.SERVICE_UNAVAILABLE,
            headers={"Retry-After": _BUSY_RETRY_AFTER_SECONDS},
        ) from None
    # Conversion explicite : le modèle ORM ne quitte jamais la couche service.
    return UserRead.model_validate(user)
