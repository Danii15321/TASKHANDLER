"""Routes publiques de santé, placées hors du préfixe `/api/v1`."""

import asyncio
import logging
from http import HTTPStatus

from fastapi import APIRouter, HTTPException

from app.db.session import SessionDependency
from app.modules.health.repository import ping_database
from app.modules.health.schemas import LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)

# Cette route est publique : une base lente ne doit pas y retenir des requêtes indéfiniment.
_READINESS_TIMEOUT_SECONDS = 2.0


# requires : rien (route publique, sans authentification).
# assigns  : rien.
# ensures  : renvoie `ok` dès que le processus est capable de répondre.
@router.get("/live", response_model=LivenessResponse)
async def read_liveness() -> LivenessResponse:
    return LivenessResponse(status="ok")


# requires : rien (route publique, sans authentification).
# assigns  : rien.
# ensures  : renvoie `ok` si PostgreSQL répond à temps ; sinon lève une erreur HTTP 503 générique,
#            sans aucun détail sur l'infrastructure.
@router.get("/ready", response_model=ReadinessResponse)
async def read_readiness(session: SessionDependency) -> ReadinessResponse:
    try:
        async with asyncio.timeout(_READINESS_TIMEOUT_SECONDS):
            await ping_database(session)
    except Exception as error:
        # Volontairement large : quelle que soit la panne, le service n'est pas prêt. Seul le
        # type est journalisé, car le message peut décrire l'hôte ou le rôle de connexion.
        logger.warning("database_unavailable", extra={"error_type": type(error).__name__})
        raise HTTPException(status_code=HTTPStatus.SERVICE_UNAVAILABLE) from error
    return ReadinessResponse(status="ok")
