"""Règles métier de l'authentification."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import PasswordHasherService
from app.modules.users.models import EMAIL_UNIQUE_INDEX, User
from app.modules.users.repository import insert_user


class EmailAlreadyRegisteredError(Exception):
    """L'adresse appartient déjà à un compte."""


# requires : `error` provient d'une écriture dans `users`.
# assigns  : rien.
# ensures  : renvoie `True` seulement si l'erreur vient de l'unicité de l'adresse.
def _is_email_conflict(error: IntegrityError) -> bool:
    # asyncpg conserve le nom de la contrainte violée dans l'exception d'origine.
    driver_error = error.orig.__cause__ if error.orig is not None else None
    return getattr(driver_error, "constraint_name", None) == EMAIL_UNIQUE_INDEX


# requires : `email` est normalisé et `password` a passé la politique de mot de passe.
# assigns  : insère le compte et valide la transaction.
# ensures  : renvoie le compte créé ; lève `EmailAlreadyRegisteredError` si l'adresse existe déjà,
#            ou `PasswordHashingBusyError` si le serveur est saturé.
async def register_user(
    session: AsyncSession, hasher: PasswordHasherService, *, email: str, password: str
) -> User:
    # Le hachage précède l'insertion : une adresse déjà inscrite prend le même temps qu'une
    # nouvelle. La réponse 409 révèle encore son existence, ce que la phase 7 corrigera.
    password_hash = await hasher.hash(password)
    try:
        user = await insert_user(session, email=email, password_hash=password_hash)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        if _is_email_conflict(error):
            # La vérification repose sur l'index unique, et non sur une lecture préalable : deux
            # inscriptions simultanées ne peuvent pas passer toutes les deux.
            raise EmailAlreadyRegisteredError from None
        raise
    return user
