"""Tâches de maintenance, à lancer hors des requêtes : `python -m app.maintenance purge`.

La purge applique les durées de conservation (RGPD, minimisation) : une donnée devenue inutile
n'est pas gardée « au cas où ». Elle tourne avec le rôle applicatif, qui a le droit de supprimer
des lignes, mais pas de toucher à la structure.
"""

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.logging_config import configure_logging
from app.db.session import create_database_engine, create_session_factory
from app.modules.auth.models import OneTimeToken, RefreshToken
from app.modules.users.models import User

audit_logger = logging.getLogger("app.audit")


@dataclass(frozen=True)
class PurgeReport:
    """Nombre de lignes supprimées par catégorie."""

    sessions: int
    one_time_tokens: int
    unverified_accounts: int


# requires : `session` est ouverte avec le rôle applicatif, `settings` est valide.
# assigns  : supprime les jetons et comptes au-delà de leur durée de conservation ; valide.
# ensures  : renvoie le nombre de lignes supprimées. Rien d'actif n'est touché : une session
#            encore valable, un lien encore utilisable ou un compte vérifié restent en place.
async def purge_expired_data(session: AsyncSession, settings: Settings) -> PurgeReport:
    # Une session compte depuis sa révocation, ou à défaut son expiration : tant qu'elle peut
    # encore servir, elle n'est jamais supprimée.
    sessions = cast(
        CursorResult[Any],
        await session.execute(
            delete(RefreshToken)
            .where(
                func.coalesce(RefreshToken.revoked_at, RefreshToken.expires_at)
                < func.now() - timedelta(days=settings.retention_session_days)
            )
            .execution_options(synchronize_session=False)
        ),
    )
    one_time_tokens = cast(
        CursorResult[Any],
        await session.execute(
            delete(OneTimeToken)
            .where(
                func.coalesce(OneTimeToken.used_at, OneTimeToken.expires_at)
                < func.now() - timedelta(days=settings.retention_one_time_token_days)
            )
            .execution_options(synchronize_session=False)
        ),
    )
    # Un compte jamais vérifié n'a pu ni se connecter, ni créer de tâche. Le supprimer libère aussi
    # une adresse qu'un tiers aurait pu réserver au nom de son propriétaire.
    unverified_accounts = cast(
        CursorResult[Any],
        await session.execute(
            delete(User)
            .where(
                User.email_verified_at.is_(None),
                User.created_at
                < func.now() - timedelta(days=settings.retention_unverified_account_days),
            )
            .execution_options(synchronize_session=False)
        ),
    )
    await session.commit()
    report = PurgeReport(
        sessions=sessions.rowcount,
        one_time_tokens=one_time_tokens.rowcount,
        unverified_accounts=unverified_accounts.rowcount,
    )
    # Des nombres seulement : aucune adresse, aucun identifiant de compte supprimé. La clé évite
    # le mot « token », que le filtre des journaux masque par précaution, comme tout secret.
    audit_logger.info(
        "retention_purge",
        extra={
            "sessions": report.sessions,
            "single_use_links": report.one_time_tokens,
            "unverified_accounts": report.unverified_accounts,
        },
    )
    return report


# requires : la configuration de l'API est disponible (environnement ou `.env`).
# assigns  : supprime les données expirées, via `purge_expired_data`.
# ensures  : la purge est faite, et la connexion fermée, même en cas d'erreur.
async def _purge() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    engine = create_database_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            await purge_expired_data(session, settings)
    finally:
        await engine.dispose()


# requires : rien.
# assigns  : exécute la commande demandée.
# ensures  : seule la commande `purge` existe ; toute autre est refusée par `argparse`.
def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.maintenance")
    parser.add_argument("command", choices=["purge"])
    parser.parse_args()
    asyncio.run(_purge())


if __name__ == "__main__":
    main()
