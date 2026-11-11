"""Accès aux données de la table `users`."""

from sqlalchemy import insert
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
