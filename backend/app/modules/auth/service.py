"""Règles métier de l'authentification : inscription, connexion et sessions."""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.passwords import PasswordHasherService
from app.core.tokens import create_access_token, hash_refresh_token, new_refresh_token
from app.modules.auth.models import (
    REASON_LOGOUT,
    REASON_LOGOUT_ALL,
    REASON_REUSE_DETECTED,
    REASON_ROTATED,
)
from app.modules.auth.repository import (
    insert_refresh_token,
    lock_refresh_token,
    revoke_all_sessions,
    revoke_refresh_token,
    revoke_session,
)
from app.modules.users.models import EMAIL_UNIQUE_INDEX, User
from app.modules.users.repository import (
    get_active_user,
    get_user_by_email,
    increment_token_version,
    insert_user,
    update_password_hash,
)

# Journal d'audit distinct : ces événements doivent pouvoir être conservés et surveillés à part.
# Il ne contient jamais l'adresse e-mail, seulement l'identifiant du compte quand il existe.
audit_logger = logging.getLogger("app.audit")


class EmailAlreadyRegisteredError(Exception):
    """L'adresse appartient déjà à un compte."""


class InvalidCredentialsError(Exception):
    """Adresse inconnue, mot de passe faux ou compte inactif : la cause n'est jamais exposée."""


class InvalidRefreshTokenError(Exception):
    """Jeton de renouvellement inconnu, expiré, révoqué ou réutilisé."""


@dataclass(frozen=True)
class IssuedTokens:
    """Paire de jetons tout juste émise. `repr=False` : ils n'apparaissent pas si on l'affiche."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


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


# requires : `email` est normalisé, `password` est normalisé en NFKC.
# assigns  : met à niveau l'empreinte si ses paramètres sont dépassés, sans valider.
# ensures  : renvoie le compte si les identifiants sont valides et le compte actif ; sinon lève
#            `InvalidCredentialsError`. Chaque cas exécute exactement une vérification argon2id,
#            pour que le temps de réponse ne révèle pas l'existence du compte.
async def authenticate(
    session: AsyncSession, hasher: PasswordHasherService, *, email: str, password: str
) -> User:
    user = await get_user_by_email(session, email)
    if user is None:
        await hasher.verify_dummy(password)
        audit_logger.info("login_failed", extra={"reason": "unknown_account"})
        raise InvalidCredentialsError

    # Le mot de passe est vérifié avant l'état du compte : un compte désactivé prend le même temps
    # qu'un autre, et sa réponse est identique.
    if not await hasher.verify(user.password_hash, password):
        audit_logger.info(
            "login_failed", extra={"reason": "wrong_password", "user_id": str(user.id)}
        )
        raise InvalidCredentialsError
    if not user.is_active:
        audit_logger.info(
            "login_failed", extra={"reason": "inactive_account", "user_id": str(user.id)}
        )
        raise InvalidCredentialsError

    # Seul moment où le mot de passe en clair est disponible : c'est ici que l'on remplace une
    # empreinte calculée avec des paramètres plus faibles que ceux d'aujourd'hui.
    if hasher.needs_rehash(user.password_hash):
        new_hash = await hasher.hash(password)
        await update_password_hash(session, user_id=user.id, password_hash=new_hash)
    return user


# requires : `session_expires_at` est postérieure à `now`.
# assigns  : insère un jeton de renouvellement, sans valider la transaction.
# ensures  : renvoie un jeton d'accès et un jeton de renouvellement liés à la session `family_id`.
async def _issue_tokens(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    token_version: int,
    family_id: uuid.UUID,
    session_expires_at: datetime,
    now: datetime,
) -> IssuedTokens:
    refresh_token = new_refresh_token()
    # L'inactivité prolonge la session, mais jamais au-delà de sa limite absolue.
    expires_at = min(now + timedelta(days=settings.refresh_token_idle_days), session_expires_at)
    await insert_refresh_token(
        session,
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_refresh_token(refresh_token),
        expires_at=expires_at,
        session_expires_at=session_expires_at,
    )
    access_token = create_access_token(
        secret=settings.jwt_secret,
        user_id=user_id,
        session_id=family_id,
        token_version=token_version,
        now=now,
    )
    return IssuedTokens(access_token=access_token, refresh_token=refresh_token)


# requires : `user` vient d'être authentifié, `now` porte un fuseau horaire.
# assigns  : crée une session et valide la transaction (y compris une empreinte mise à niveau).
# ensures  : renvoie la première paire de jetons d'une nouvelle session.
async def open_session(
    session: AsyncSession, settings: Settings, user: User, *, now: datetime
) -> IssuedTokens:
    family_id = uuid.uuid4()
    tokens = await _issue_tokens(
        session,
        settings,
        user_id=user.id,
        token_version=user.token_version,
        family_id=family_id,
        session_expires_at=now + timedelta(days=settings.session_max_days),
        now=now,
    )
    await session.commit()
    audit_logger.info(
        "login_succeeded", extra={"user_id": str(user.id), "session_id": str(family_id)}
    )
    return tokens


# requires : `refresh_token` a le format d'un jeton, `now` porte un fuseau horaire.
# assigns  : révoque le jeton utilisé et en enregistre un nouveau, dans une seule transaction ;
#            en cas de réutilisation, révoque toute la session.
# ensures  : renvoie une nouvelle paire de jetons ; sinon lève `InvalidRefreshTokenError`.
async def rotate_refresh_token(
    session: AsyncSession, settings: Settings, *, refresh_token: str, now: datetime
) -> IssuedTokens:
    # Le verrou `FOR UPDATE` est tenu jusqu'au commit : un second renouvellement simultané du
    # même jeton attend, puis voit ce jeton révoqué, et il est traité comme une réutilisation.
    stored = await lock_refresh_token(session, hash_refresh_token(refresh_token))
    if stored is None:
        raise InvalidRefreshTokenError

    if stored.revoked_at is not None:
        if stored.revocation_reason == REASON_ROTATED:
            # Un jeton déjà échangé ne revient que si deux personnes le détiennent : l'une d'elles
            # est un attaquant, et rien ne permet de savoir laquelle. Toute la session est coupée.
            await revoke_session(
                session,
                user_id=stored.user_id,
                family_id=stored.family_id,
                reason=REASON_REUSE_DETECTED,
            )
            await session.commit()
            audit_logger.warning(
                "refresh_token_reused",
                extra={"user_id": str(stored.user_id), "session_id": str(stored.family_id)},
            )
        raise InvalidRefreshTokenError

    if stored.expires_at <= now or stored.session_expires_at <= now:
        raise InvalidRefreshTokenError
    user = await get_active_user(session, stored.user_id)
    if user is None:
        raise InvalidRefreshTokenError

    await revoke_refresh_token(session, token_id=stored.id, reason=REASON_ROTATED)
    tokens = await _issue_tokens(
        session,
        settings,
        user_id=user.id,
        token_version=user.token_version,
        family_id=stored.family_id,
        session_expires_at=stored.session_expires_at,
        now=now,
    )
    await session.commit()
    return tokens


# requires : `user_id` et `session_id` viennent d'un jeton d'accès vérifié.
# assigns  : révoque la session et valide la transaction.
# ensures  : le jeton de renouvellement et les jetons d'accès de cette session sont refusés.
async def close_session(
    session: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID
) -> None:
    await revoke_session(session, user_id=user_id, family_id=session_id, reason=REASON_LOGOUT)
    await session.commit()
    audit_logger.info("logout", extra={"user_id": str(user_id), "session_id": str(session_id)})


# requires : `user_id` vient d'un jeton d'accès vérifié.
# assigns  : révoque toutes les sessions, incrémente `token_version` et valide la transaction.
# ensures  : plus aucun jeton émis jusqu'ici pour ce compte n'est accepté, sur aucun appareil.
async def close_all_sessions(session: AsyncSession, *, user_id: uuid.UUID) -> None:
    await revoke_all_sessions(session, user_id=user_id, reason=REASON_LOGOUT_ALL)
    await increment_token_version(session, user_id=user_id)
    await session.commit()
    audit_logger.info("logout_all", extra={"user_id": str(user_id)})
