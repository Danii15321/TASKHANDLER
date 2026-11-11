"""Parcours de compte par e-mail : inscription, vérification et réinitialisation.

Règle commune : la réponse HTTP ne dépend jamais de l'existence de l'adresse. Les routes publiques
répondent `202` tout de suite, et le travail qui en dépend (base, hachage, e-mail) s'exécute en
tâche d'arrière-plan, après l'envoi de la réponse : son temps ne s'observe pas de l'extérieur.
"""

import logging
import uuid
from collections.abc import Awaitable
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.email import EmailSender, OutgoingEmail
from app.core.passwords import PasswordHasherService
from app.core.rate_limit import RateLimiter, RateLimitExceededError
from app.core.tokens import hash_opaque_token, new_opaque_token
from app.db.session import SessionFactory
from app.modules.auth.emails import account_exists_email, password_reset_email, verification_email
from app.modules.auth.limits import (
    forgot_password_email_limit,
    forgot_password_ip_limit,
    verification_resend_limit,
)
from app.modules.auth.models import (
    PURPOSE_EMAIL_VERIFICATION,
    PURPOSE_PASSWORD_RESET,
    REASON_PASSWORD_RESET,
)
from app.modules.auth.password_policy import ensure_password_unrelated_to_email
from app.modules.auth.repository import (
    consume_one_time_token,
    discard_unused_tokens,
    insert_one_time_token,
    revoke_all_sessions,
)
from app.modules.users.models import EMAIL_UNIQUE_INDEX
from app.modules.users.repository import (
    get_active_user,
    get_user_by_email,
    increment_token_version,
    insert_user,
    mark_email_verified,
    update_password_hash,
)

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("app.audit")


class InvalidOneTimeTokenError(Exception):
    """Lien inconnu, expiré, déjà utilisé ou destiné à un autre usage : la cause n'est pas dite."""


class PasswordRejectedError(Exception):
    """Le nouveau mot de passe enfreint une règle qui dépend du compte (il reprend l'adresse)."""


# requires : `error` provient d'une écriture dans `users`.
# assigns  : rien.
# ensures  : renvoie `True` seulement si l'erreur vient de l'unicité de l'adresse.
def _is_email_conflict(error: IntegrityError) -> bool:
    # asyncpg conserve le nom de la contrainte violée dans l'exception d'origine.
    driver_error = error.orig.__cause__ if error.orig is not None else None
    return getattr(driver_error, "constraint_name", None) == EMAIL_UNIQUE_INDEX


# requires : `task_name` décrit la tâche, `work` est la tâche à exécuter.
# assigns  : ce que modifie `work`.
# ensures  : exécute `work` ; une erreur est journalisée (type seulement) au lieu de remonter, car
#            la réponse est déjà partie et personne ne pourrait la traiter.
async def _run_in_background(task_name: str, work: Awaitable[None]) -> None:
    try:
        await work
    except Exception as error:
        logger.error(
            "background_task_failed",
            extra={"task": task_name, "error_type": type(error).__name__},
        )


# requires : `ttl` respecte les durées maximales imposées par la table.
# assigns  : supprime les anciens jetons inutilisés de cet usage, puis en insère un nouveau.
# ensures  : renvoie la valeur du nouveau jeton, seul valable pour cet usage ; seule son empreinte
#            est enregistrée.
async def _issue_one_time_token(
    session: AsyncSession, *, user_id: uuid.UUID, purpose: str, ttl: timedelta
) -> str:
    await discard_unused_tokens(session, user_id=user_id, purpose=purpose)
    token = new_opaque_token()
    await insert_one_time_token(
        session,
        user_id=user_id,
        purpose=purpose,
        token_hash=hash_opaque_token(token),
        ttl=ttl,
    )
    return token


# requires : `email` est normalisé, `password` a passé la politique de mot de passe.
# assigns  : crée le compte et un jeton de vérification, ou un nouveau jeton si le compte attend
#            encore sa vérification ; envoie un e-mail.
# ensures  : l'adresse reçoit, selon le cas, un lien de vérification ou un avertissement « compte
#            déjà existant ». Rien n'est renvoyé : la réponse HTTP est déjà partie.
async def _register(
    session_factory: SessionFactory,
    hasher: PasswordHasherService,
    sender: EmailSender,
    settings: Settings,
    *,
    email: str,
    password: str,
) -> None:
    ttl = timedelta(hours=settings.email_verification_ttl_hours)
    password_hash = await hasher.hash(password)
    outgoing: OutgoingEmail
    async with session_factory() as session:
        try:
            user = await insert_user(session, email=email, password_hash=password_hash)
            token = await _issue_one_time_token(
                session, user_id=user.id, purpose=PURPOSE_EMAIL_VERIFICATION, ttl=ttl
            )
            await session.commit()
            outgoing = verification_email(settings, email, token)
        except IntegrityError as error:
            await session.rollback()
            if not _is_email_conflict(error):
                raise
            # L'adresse existe : seul son propriétaire, qui lit cet e-mail, l'apprend.
            existing = await get_user_by_email(session, email)
            if existing is not None and existing.is_active and existing.email_verified_at is None:
                token = await _issue_one_time_token(
                    session, user_id=existing.id, purpose=PURPOSE_EMAIL_VERIFICATION, ttl=ttl
                )
                await session.commit()
                outgoing = verification_email(settings, email, token)
            else:
                outgoing = account_exists_email(settings, email)
    await sender.send(outgoing)


# requires : voir `_register` ; appelée en tâche d'arrière-plan, après la réponse `202`.
# assigns  : voir `_register`.
# ensures  : voir `_register` ; une erreur est journalisée, jamais renvoyée au client.
async def process_registration(
    session_factory: SessionFactory,
    hasher: PasswordHasherService,
    sender: EmailSender,
    settings: Settings,
    *,
    email: str,
    password: str,
) -> None:
    await _run_in_background(
        "registration",
        _register(session_factory, hasher, sender, settings, email=email, password=password),
    )


# requires : `token` a le format d'un jeton opaque.
# assigns  : consomme le jeton et marque l'adresse vérifiée, puis valide la transaction.
# ensures  : l'adresse est vérifiée ; lève `InvalidOneTimeTokenError` si le lien n'est pas valable.
async def verify_email(session: AsyncSession, *, token: str) -> None:
    user_id = await consume_one_time_token(
        session, token_hash=hash_opaque_token(token), purpose=PURPOSE_EMAIL_VERIFICATION
    )
    if user_id is None:
        raise InvalidOneTimeTokenError
    await mark_email_verified(session, user_id=user_id)
    await session.commit()
    audit_logger.info("email_verified", extra={"user_id": str(user_id)})


# requires : `user_id` vient de se connecter avec le bon mot de passe, sans adresse vérifiée.
# assigns  : enregistre le renvoi dans le compteur du compte ; émet un jeton ; envoie un e-mail.
# ensures  : un nouveau lien est envoyé, au plus 3 fois par heure et par compte.
async def _resend_verification(
    session_factory: SessionFactory,
    sender: EmailSender,
    limiter: RateLimiter,
    settings: Settings,
    *,
    user_id: uuid.UUID,
) -> None:
    try:
        await limiter.hit(verification_resend_limit(settings), str(user_id))
    except RateLimitExceededError:
        # Limite atteinte : le lien précédent reste valable, il n'y a rien d'autre à faire.
        return
    async with session_factory() as session:
        user = await get_active_user(session, user_id)
        if user is None or user.email_verified_at is not None:
            return
        token = await _issue_one_time_token(
            session,
            user_id=user.id,
            purpose=PURPOSE_EMAIL_VERIFICATION,
            ttl=timedelta(hours=settings.email_verification_ttl_hours),
        )
        await session.commit()
        recipient = user.email
    await sender.send(verification_email(settings, recipient, token))


# requires : voir `_resend_verification` ; appelée en tâche d'arrière-plan.
# assigns  : voir `_resend_verification`.
# ensures  : voir `_resend_verification` ; une erreur est journalisée, jamais renvoyée.
async def process_verification_resend(
    session_factory: SessionFactory,
    sender: EmailSender,
    limiter: RateLimiter,
    settings: Settings,
    *,
    user_id: uuid.UUID,
) -> None:
    await _run_in_background(
        "verification_resend",
        _resend_verification(session_factory, sender, limiter, settings, user_id=user_id),
    )


# requires : `email` est normalisé, `client_ip` vient de `get_client_ip`.
# assigns  : enregistre la demande dans les compteurs de « mot de passe oublié ».
# ensures  : se termine sous les limites ; sinon lève une erreur du limiteur. La limite par adresse
#            empêche d'inonder la boîte d'une victime d'e-mails de réinitialisation.
async def guard_password_reset_request(
    limiter: RateLimiter, settings: Settings, *, email: str, client_ip: str
) -> None:
    await limiter.hit(forgot_password_ip_limit(settings), client_ip)
    await limiter.hit(forgot_password_email_limit(settings), email)


# requires : `email` est normalisé.
# assigns  : émet un jeton de réinitialisation et envoie un e-mail, si un compte actif existe.
# ensures  : un compte actif reçoit un lien valable 30 minutes au plus ; une adresse inconnue ne
#            reçoit rien. Dans les deux cas, la réponse HTTP était déjà la même.
async def _request_password_reset(
    session_factory: SessionFactory, sender: EmailSender, settings: Settings, *, email: str
) -> None:
    async with session_factory() as session:
        user = await get_user_by_email(session, email)
        if user is None or not user.is_active:
            return
        token = await _issue_one_time_token(
            session,
            user_id=user.id,
            purpose=PURPOSE_PASSWORD_RESET,
            ttl=timedelta(minutes=settings.password_reset_ttl_minutes),
        )
        await session.commit()
        user_id, recipient = user.id, user.email
    await sender.send(password_reset_email(settings, recipient, token))
    audit_logger.info("password_reset_requested", extra={"user_id": str(user_id)})


# requires : voir `_request_password_reset` ; appelée en tâche d'arrière-plan.
# assigns  : voir `_request_password_reset`.
# ensures  : voir `_request_password_reset` ; une erreur est journalisée, jamais renvoyée.
async def process_password_reset_request(
    session_factory: SessionFactory, sender: EmailSender, settings: Settings, *, email: str
) -> None:
    await _run_in_background(
        "password_reset_request",
        _request_password_reset(session_factory, sender, settings, email=email),
    )


# requires : `token` a le format d'un jeton opaque, `new_password` a passé la politique.
# assigns  : consomme le jeton ; remplace l'empreinte ; marque l'adresse vérifiée ; révoque toutes
#            les sessions et incrémente `token_version` ; valide le tout en une transaction.
# ensures  : le nouveau mot de passe est actif et aucune ancienne session ne survit. Lève
#            `InvalidOneTimeTokenError` si le lien n'est pas valable, `PasswordRejectedError` si
#            le mot de passe reprend l'adresse : la transaction est alors annulée, et le lien
#            reste utilisable pour un autre essai.
async def reset_password(
    session: AsyncSession, hasher: PasswordHasherService, *, token: str, new_password: str
) -> None:
    user_id = await consume_one_time_token(
        session, token_hash=hash_opaque_token(token), purpose=PURPOSE_PASSWORD_RESET
    )
    if user_id is None:
        raise InvalidOneTimeTokenError
    user = await get_active_user(session, user_id)
    if user is None:
        raise InvalidOneTimeTokenError
    try:
        ensure_password_unrelated_to_email(new_password, user.email)
    except ValueError:
        # Annulation explicite ici, et non laissée à l'appelant : c'est cette fonction qui promet
        # que le lien reste utilisable pour un nouvel essai.
        await session.rollback()
        raise PasswordRejectedError from None

    await update_password_hash(
        session, user_id=user.id, password_hash=await hasher.hash(new_password)
    )
    # Le lien a été reçu dans la boîte de l'adresse : elle est donc vérifiée.
    await mark_email_verified(session, user_id=user.id)
    await discard_unused_tokens(session, user_id=user.id, purpose=PURPOSE_PASSWORD_RESET)
    # Quiconque avait volé une session la perd : c'est souvent la raison même du changement.
    await revoke_all_sessions(session, user_id=user.id, reason=REASON_PASSWORD_RESET)
    await increment_token_version(session, user_id=user.id)
    await session.commit()
    audit_logger.info("password_reset", extra={"user_id": str(user.id)})
