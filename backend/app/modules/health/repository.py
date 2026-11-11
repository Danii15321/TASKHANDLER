"""Accès aux données de la sonde de santé."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# requires : `session` est une session ouverte.
# assigns  : rien (lecture sans effet de bord).
# ensures  : se termine si PostgreSQL répond ; sinon, propage l'erreur de connexion.
async def ping_database(session: AsyncSession) -> None:
    await session.execute(text("SELECT 1"))
