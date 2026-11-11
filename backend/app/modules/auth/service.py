"""Règles métier de l'authentification : connexion et sessions.

Les parcours par e-mail (inscription, vérification, réinitialisation) sont dans `account_flows`.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.passwords import PasswordHasherService
from app.core.rate_limit import RateLimiter
from app.core.tokens import create_access_token, hash_opaque_token, new_opaque_token
from app.modules.auth.limits import (
    LOGIN_BACKOFF_SCOPE,
    login_account_limit,
    login_ip_limit,
    refresh_user_limit,
    register_email_limit,
    register_ip_limit,
)
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
from app.modules.users.models import User
from app.modules.users.repository import (
    get_active_user,
    get_user_by_email,
    increment_token_version,
    update_password_hash,
)

# Journal d'audit distinct : ces événements doivent pouvoir être conservés et surveillés à part.
# Il ne contient jamais l'adresse e-mail, seulement l'identifiant du compte quand il existe.
audit_logger = logging.getLogger("app.audit")


class InvalidCredentialsError(Exception):
    """Adresse inconnue, mot de passe faux ou compte inactif : la cause n'est jamais exposée."""


class EmailNotVerifiedError(Exception):
    """Mot de passe correct, mais adresse jamais vérifiée : levée seulement après la vérification
    du mot de passe, donc visible uniquement de quelqu'un qui le connaît déjà."""

    # requires : `user_id` désigne un compte actif dont l'adresse n'est pas vérifiée.
    # assigns  : rien.
    # ensures  : l'exception porte l'identifiant du compte, pour renvoyer un lien de vérification.
    def __init__(self, user_id: uuid.UUID) -> None:
        super().__init__("adresse non vérifiée")
        self.user_id = user_id


class InvalidRefreshTokenError(Exception):
    """Jeton de renouvellement inconnu, expiré, révoqué ou réutilisé."""


@dataclass(frozen=True)
class IssuedTokens:
    """Paire de jetons tout juste émise. `repr=False` : ils n'apparaissent pas si on l'affiche."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


# requires : `email` est normalisé, `client_ip` vient de `get_client_ip`.
# assigns  : enregistre la demande dans les compteurs d'inscription.
# ensures  : se termine si l'IP et l'adresse sont sous leurs limites ; sinon lève une erreur du
#            limiteur. Appelé avant le hachage : une inscription refusée ne coûte rien au serveur.
async def guard_registration(
    limiter: RateLimiter, settings: Settings, *, email: str, client_ip: str
) -> None:
    await limiter.hit(register_ip_limit(settings), client_ip)
    await limiter.hit(register_email_limit(settings), email)


# requires : `email` est normalisé, `password` est normalisé en NFKC.
# assigns  : met à niveau l'empreinte si ses paramètres sont dépassés, sans valider.
# ensures  : renvoie le compte si les identifiants sont valides et le compte actif ; sinon lève
#            `InvalidCredentialsError`, ou `EmailNotVerifiedError` si seule la vérification de
#            l'adresse manque. Chaque cas exécute exactement une vérification argon2id,
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
    if user.email_verified_at is None:
        audit_logger.info(
            "login_failed", extra={"reason": "email_not_verified", "user_id": str(user.id)}
        )
        raise EmailNotVerifiedError(user.id)

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
    refresh_token = new_opaque_token()
    # L'inactivité prolonge la session, mais jamais au-delà de sa limite absolue.
    expires_at = min(now + timedelta(days=settings.refresh_token_idle_days), session_expires_at)
    await insert_refresh_token(
        session,
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_opaque_token(refresh_token),
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
# ensures  : renvoie une nouvelle paire de jetons ; sinon lève `InvalidRefreshTokenError`, ou une
#            erreur du limiteur si l'utilisateur renouvelle trop souvent.
async def rotate_refresh_token(
    session: AsyncSession,
    settings: Settings,
    limiter: RateLimiter,
    *,
    refresh_token: str,
    now: datetime,
) -> IssuedTokens:
    # Le verrou `FOR UPDATE` est tenu jusqu'au commit : un second renouvellement simultané du
    # même jeton attend, puis voit ce jeton révoqué, et il est traité comme une réutilisation.
    stored = await lock_refresh_token(session, hash_opaque_token(refresh_token))
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
    # Limite appliquée après la détection de réutilisation : un attaquant ne doit pas pouvoir
    # échapper à la coupure de la session en épuisant d'abord la limite.
    await limiter.hit(refresh_user_limit(settings), str(user.id))

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


# requires : `email` et `password` sont normalisés, `client_ip` vient de `get_client_ip`.
# assigns  : met à jour les compteurs et la temporisation ; ouvre une session en cas de succès.
# ensures  : renvoie une paire de jetons ; lève `InvalidCredentialsError` si l'authentification
#            échoue, `EmailNotVerifiedError` si l'adresse n'est pas vérifiée, ou une erreur du
#            limiteur si une limite ou la temporisation s'applique.
async def log_in(
    session: AsyncSession,
    hasher: PasswordHasherService,
    limiter: RateLimiter,
    settings: Settings,
    *,
    email: str,
    password: str,
    client_ip: str,
    now: datetime,
) -> IssuedTokens:
    # Couple IP + compte : une attaque menée depuis une autre IP ne ralentit jamais la victime,
    # qui se connecte depuis la sienne. Personne ne peut ainsi bloquer un compte à distance.
    pair = f"{client_ip}|{email}"
    await limiter.hit(login_ip_limit(settings), client_ip)
    await limiter.hit(login_account_limit(settings), pair)
    await limiter.ensure_backoff_elapsed(LOGIN_BACKOFF_SCOPE, pair)
    try:
        user = await authenticate(session, hasher, email=email, password=password)
    except InvalidCredentialsError:
        # Le compte inconnu est compté comme les autres : la temporisation ne révèle rien.
        await limiter.record_failure(LOGIN_BACKOFF_SCOPE, pair)
        raise
    except EmailNotVerifiedError:
        # Le mot de passe était correct : ce n'est pas une tentative de force brute.
        await limiter.reset_failures(LOGIN_BACKOFF_SCOPE, pair)
        raise
    await limiter.reset_failures(LOGIN_BACKOFF_SCOPE, pair)
    return await open_session(session, settings, user, now=now)
