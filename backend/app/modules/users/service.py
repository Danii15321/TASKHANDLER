"""Règles métier du compte : réauthentification, mot de passe, suppression et export."""

import logging
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.passwords import PasswordHasherService
from app.core.rate_limit import Limit, RateLimiter
from app.modules.auth.models import REASON_PASSWORD_CHANGE
from app.modules.auth.password_policy import ensure_password_unrelated_to_email
from app.modules.auth.repository import list_user_sessions, revoke_all_sessions
from app.modules.tasks.models import TaskDay
from app.modules.tasks.repository import list_all_tasks_with_days
from app.modules.users.models import User
from app.modules.users.repository import (
    delete_user,
    increment_token_version,
    update_password_hash,
)
from app.modules.users.schemas import (
    AccountExport,
    ExportedSession,
    ExportedTask,
    ExportedTaskDay,
    UserRead,
)

audit_logger = logging.getLogger("app.audit")

# Un jeton d'accès volé ne doit pas permettre de deviner le mot de passe à la chaîne : 5 essais de
# réauthentification par minute et par compte, puis 429.
_REAUTHENTICATION_LIMIT = Limit("reauthentication", 5, 60)
# L'export lit toutes les données du compte : 5 par heure suffisent, et bornent son coût.
EXPORT_LIMIT = Limit("account-export", 5, 3600)


class ReauthenticationFailedError(Exception):
    """Le mot de passe actuel fourni ne correspond pas : l'action sensible est refusée."""


class PasswordRejectedError(Exception):
    """Le nouveau mot de passe reprend l'adresse e-mail du compte."""


# requires : `user` vient de `get_current_user`, `password` est normalisé en NFKC.
# assigns  : enregistre la tentative dans le compteur du compte.
# ensures  : se termine si le mot de passe est le bon ; sinon lève `ReauthenticationFailedError`,
#            ou une erreur du limiteur au-delà de 5 essais par minute.
async def _reauthenticate(
    hasher: PasswordHasherService, limiter: RateLimiter, *, user: User, password: str
) -> None:
    await limiter.hit(_REAUTHENTICATION_LIMIT, str(user.id))
    if not await hasher.verify(user.password_hash, password):
        audit_logger.warning("reauthentication_failed", extra={"user_id": str(user.id)})
        raise ReauthenticationFailedError


# requires : `user` vient de `get_current_user` ; `new_password` a passé la politique.
# assigns  : remplace l'empreinte, révoque toutes les sessions, incrémente `token_version`, et
#            valide la transaction.
# ensures  : le nouveau mot de passe est actif et plus aucune session ne survit, y compris celle
#            de cette requête. Lève `ReauthenticationFailedError`, `PasswordRejectedError` ou une
#            erreur du limiteur, sans rien modifier.
async def change_password(
    session: AsyncSession,
    hasher: PasswordHasherService,
    limiter: RateLimiter,
    *,
    user: User,
    current_password: str,
    new_password: str,
) -> None:
    await _reauthenticate(hasher, limiter, user=user, password=current_password)
    try:
        ensure_password_unrelated_to_email(new_password, user.email)
    except ValueError:
        raise PasswordRejectedError from None
    await update_password_hash(
        session, user_id=user.id, password_hash=await hasher.hash(new_password)
    )
    # Si le mot de passe change parce qu'un appareil a été perdu ou volé, ses sessions tombent.
    await revoke_all_sessions(session, user_id=user.id, reason=REASON_PASSWORD_CHANGE)
    await increment_token_version(session, user_id=user.id)
    await session.commit()
    audit_logger.info("password_changed", extra={"user_id": str(user.id)})


# requires : `user` vient de `get_current_user`.
# assigns  : supprime le compte et, en cascade, toutes ses données ; valide la transaction.
# ensures  : il ne reste rien du compte en base ; lève `ReauthenticationFailedError` ou une erreur
#            du limiteur sans rien supprimer.
async def delete_account(
    session: AsyncSession,
    hasher: PasswordHasherService,
    limiter: RateLimiter,
    *,
    user: User,
    password: str,
) -> None:
    await _reauthenticate(hasher, limiter, user=user, password=password)
    user_id = user.id
    await delete_user(session, user_id=user_id)
    await session.commit()
    # Seul l'identifiant, désormais orphelin, est journalisé : jamais l'adresse supprimée.
    audit_logger.info("account_deleted", extra={"user_id": str(user_id)})


# requires : `user` vient de `get_current_user` (la session est liée à lui pour la RLS).
# assigns  : rien (lecture seule).
# ensures  : renvoie toutes les données personnelles du compte, dans un format réutilisable :
#            compte, tâches avec description et jours, sessions sans empreinte de jeton.
async def export_account(session: AsyncSession, *, user: User) -> AccountExport:
    tasks, days = await list_all_tasks_with_days(session, user_id=user.id)
    days_by_task: defaultdict[object, list[TaskDay]] = defaultdict(list)
    for task_day in days:
        days_by_task[task_day.task_id].append(task_day)
    sessions = await list_user_sessions(session, user_id=user.id)
    audit_logger.info("account_exported", extra={"user_id": str(user.id)})
    return AccountExport(
        exported_at=datetime.now(UTC),
        account=UserRead.model_validate(user),
        tasks=[
            ExportedTask(
                id=task.id,
                title=task.title,
                description=task.description,
                priority=task.priority,
                created_at=task.created_at,
                updated_at=task.updated_at,
                days=[ExportedTaskDay.model_validate(day) for day in days_by_task[task.id]],
            )
            for task in tasks
        ],
        sessions=[ExportedSession.model_validate(token) for token in sessions],
    )
