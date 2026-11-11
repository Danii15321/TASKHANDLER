"""Accès aux données de la table `users`."""

import uuid

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User


# requires : `email` est normalisé, `password_hash` est une empreinte argon2id.
# assigns  : insère une ligne dans `users`, sans valider la transaction.
# ensures  : renvoie le compte créé, avec les valeurs calculées par PostgreSQL ; lève
#            `IntegrityError` si l'adresse existe déjà ou si une contrainte est violée.
async def insert_user(session: AsyncSession, *, email: str, password_hash: str) -> User:
    # `RETURNING` : un seul aller-retour pour obtenir l'identifiant et les dates par défaut.
    statement = insert(User).values(email=email, password_hash=password_hash).returning(User)
    return (await session.execute(statement)).scalar_one()


# requires : `email` est normalisé et en minuscules.
# assigns  : rien (lecture seule).
# ensures  : renvoie le compte de cette adresse, ou `None`. `lower(email)` utilise l'index unique.
async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    statement = select(User).where(func.lower(User.email) == email)
    return (await session.execute(statement)).scalar_one_or_none()


# requires : rien.
# assigns  : rien (lecture seule).
# ensures  : renvoie le compte s'il existe et qu'il est actif, sinon `None`.
async def get_active_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    statement = select(User).where(User.id == user_id, User.is_active.is_(True))
    return (await session.execute(statement)).scalar_one_or_none()


# requires : `password_hash` est une empreinte argon2id.
# assigns  : remplace l'empreinte du compte, sans valider la transaction.
# ensures  : le compte utilise la nouvelle empreinte.
async def update_password_hash(
    session: AsyncSession, *, user_id: uuid.UUID, password_hash: str
) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(password_hash=password_hash)
        .execution_options(synchronize_session=False)
    )


# requires : rien.
# assigns  : incrémente `token_version` du compte, sans valider la transaction.
# ensures  : tous les jetons d'accès déjà émis pour ce compte deviennent invalides.
async def increment_token_version(session: AsyncSession, *, user_id: uuid.UUID) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(token_version=User.token_version + 1)
        .execution_options(synchronize_session=False)
    )
