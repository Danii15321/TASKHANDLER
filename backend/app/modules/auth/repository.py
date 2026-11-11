"""Accès aux données des sessions. Chaque requête porte le propriétaire dans son `WHERE`."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import OneTimeToken, RefreshToken
from app.modules.users.models import User


# requires : `token_hash` est l'empreinte SHA-256 d'un jeton neuf.
# assigns  : insère une ligne dans `refresh_tokens`, sans valider la transaction.
# ensures  : le jeton est enregistré, actif jusqu'à `expires_at`.
async def insert_refresh_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    session_expires_at: datetime,
) -> None:
    await session.execute(
        insert(RefreshToken).values(
            user_id=user_id,
            family_id=family_id,
            token_hash=token_hash,
            expires_at=expires_at,
            session_expires_at=session_expires_at,
        )
    )


# requires : une transaction est ouverte.
# assigns  : verrouille la ligne trouvée jusqu'à la fin de la transaction.
# ensures  : renvoie le jeton correspondant à l'empreinte, ou `None`. Deux renouvellements
#            simultanés du même jeton sont ainsi traités l'un après l'autre, jamais ensemble.
async def lock_refresh_token(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


# requires : `reason` est une raison connue de la table.
# assigns  : révoque un jeton encore actif, sans valider la transaction.
# ensures  : le jeton ne peut plus servir ; un jeton déjà révoqué garde sa première raison.
async def revoke_refresh_token(session: AsyncSession, *, token_id: uuid.UUID, reason: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.id == token_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now(), revocation_reason=reason)
        .execution_options(synchronize_session=False)
    )


# requires : `reason` est une raison connue de la table.
# assigns  : révoque les jetons actifs de cette session, sans valider la transaction.
# ensures  : la session est coupée ; seuls les jetons de `user_id` peuvent être touchés.
async def revoke_session(
    session: AsyncSession, *, user_id: uuid.UUID, family_id: uuid.UUID, reason: str
) -> None:
    await session.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == user_id,
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=func.now(), revocation_reason=reason)
        .execution_options(synchronize_session=False)
    )


# requires : `reason` est une raison connue de la table.
# assigns  : révoque tous les jetons actifs de l'utilisateur, sans valider la transaction.
# ensures  : toutes les sessions de `user_id` sont coupées, et aucune autre.
async def revoke_all_sessions(session: AsyncSession, *, user_id: uuid.UUID, reason: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=func.now(), revocation_reason=reason)
        .execution_options(synchronize_session=False)
    )


# requires : les arguments viennent d'un jeton d'accès dont la signature a été vérifiée.
# assigns  : rien (lecture seule).
# ensures  : renvoie l'utilisateur seulement s'il est actif, si sa version de jeton correspond, et
#            si la session existe encore pour lui ; sinon `None`. Tout est vérifié dans une seule
#            requête : il n'y a pas de lecture suivie d'un contrôle en Python.
async def get_authenticated_user(
    session: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID, token_version: int
) -> User | None:
    session_is_active = exists().where(
        RefreshToken.user_id == user_id,
        RefreshToken.family_id == session_id,
        RefreshToken.revoked_at.is_(None),
        RefreshToken.expires_at > func.now(),
    )
    statement = select(User).where(
        User.id == user_id,
        User.is_active.is_(True),
        User.token_version == token_version,
        session_is_active,
    )
    return (await session.execute(statement)).scalar_one_or_none()


# requires : `token_hash` est l'empreinte SHA-256 d'un jeton neuf, `purpose` un usage connu, `ttl`
#            respecte la durée maximale de cet usage.
# assigns  : insère un jeton à usage unique, sans valider la transaction.
# ensures  : le jeton est enregistré, utilisable une fois pendant `ttl`. L'expiration est calculée
#            par PostgreSQL (`now() + ttl`), qui date aussi la création : une seule horloge, donc
#            aucun décalage ne peut faire dépasser la durée maximale vérifiée par la table.
async def insert_one_time_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    purpose: str,
    token_hash: str,
    ttl: timedelta,
) -> None:
    await session.execute(
        insert(OneTimeToken).values(
            user_id=user_id, purpose=purpose, token_hash=token_hash, expires_at=func.now() + ttl
        )
    )


# requires : `purpose` est un usage connu.
# assigns  : supprime les jetons encore inutilisés de cet utilisateur pour cet usage.
# ensures  : seul le prochain jeton émis sera valable : un ancien lien resté dans une boîte mail
#            ne sert plus à rien. Seules les lignes de `user_id` sont touchées.
async def discard_unused_tokens(session: AsyncSession, *, user_id: uuid.UUID, purpose: str) -> None:
    await session.execute(
        delete(OneTimeToken)
        .where(
            OneTimeToken.user_id == user_id,
            OneTimeToken.purpose == purpose,
            OneTimeToken.used_at.is_(None),
        )
        .execution_options(synchronize_session=False)
    )


# requires : `token_hash` est l'empreinte du jeton reçu, `purpose` l'usage attendu par la route.
# assigns  : marque le jeton comme utilisé, sans valider la transaction.
# ensures  : renvoie le propriétaire si le jeton existait, pour cet usage, inutilisé et non expiré ;
#            sinon `None`. Une seule instruction `UPDATE … RETURNING` : deux requêtes simultanées
#            ne peuvent pas consommer le même jeton.
async def consume_one_time_token(
    session: AsyncSession, *, token_hash: str, purpose: str
) -> uuid.UUID | None:
    statement = (
        update(OneTimeToken)
        .where(
            OneTimeToken.token_hash == token_hash,
            OneTimeToken.purpose == purpose,
            OneTimeToken.used_at.is_(None),
            OneTimeToken.expires_at > func.now(),
        )
        .values(used_at=func.now())
        .returning(OneTimeToken.user_id)
        .execution_options(synchronize_session=False)
    )
    return (await session.execute(statement)).scalar_one_or_none()


# requires : `user_id` vient d'un jeton d'accès vérifié.
# assigns  : rien (lecture seule).
# ensures  : renvoie les jetons de session de `user_id`, du plus ancien au plus récent ; l'appelant
#            n'en expose que les dates, jamais l'empreinte.
async def list_user_sessions(session: AsyncSession, *, user_id: uuid.UUID) -> list[RefreshToken]:
    statement = (
        select(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .order_by(RefreshToken.created_at, RefreshToken.id)
    )
    return list((await session.execute(statement)).scalars())
