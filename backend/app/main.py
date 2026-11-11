"""Assemblage de l'application.

Il n'y a pas de variable globale `app` : la configuration est chargée à l'appel de la fabrique,
ce qui permet aux tests de construire une application pour chaque environnement.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.core.body_limit import BodySizeLimitMiddleware
from app.core.config import Settings
from app.core.email import SmtpEmailSender
from app.core.errors import register_exception_handlers
from app.core.logging_config import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.passwords import PasswordHasherService
from app.core.rate_limit import RateLimiter, create_redis_client
from app.db.session import create_database_engine, create_session_factory
from app.modules.auth.router import router as auth_router
from app.modules.auth.router import session_router
from app.modules.health.router import router as health_router
from app.modules.tasks.router import planning_router
from app.modules.tasks.router import router as tasks_router


# requires : `app.state` a été rempli par `create_app`.
# assigns  : calcule l'empreinte leurre au démarrage ; ferme PostgreSQL et Redis à l'arrêt.
# ensures  : la première connexion à un compte inconnu n'est pas plus lente que les autres, et
#            aucune connexion à PostgreSQL ne survit à l'arrêt du serveur.
@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.password_hasher.prepare()
    yield
    await app.state.engine.dispose()
    await app.state.redis.aclose()


# requires : `settings` est valide ; si `None`, la configuration vient de l'environnement.
# assigns  : la configuration globale des journaux, le moteur et les sessions de `app.state`.
# ensures  : renvoie l'application assemblée, ou lève `ValidationError` si la configuration
#            est invalide, ce qui empêche le démarrage.
def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings if settings is not None else Settings()
    configure_logging(resolved_settings.log_level)

    # Sans documentation, ces trois chemins renvoient 404 : le schéma complet de l'API ne doit
    # pas être offert à un attaquant en production.
    docs_enabled = resolved_settings.api_docs_enabled
    app = FastAPI(
        title="TaskHandler API",
        # Le mode debug afficherait les traces d'erreur au client : il n'est jamais activé.
        debug=False,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=_lifespan,
    )

    # Le moteur n'ouvre aucune connexion ici : `/health/live` reste disponible si la base est
    # en panne, et c'est `/health/ready` qui signale cette panne.
    app.state.settings = resolved_settings
    app.state.engine = create_database_engine(resolved_settings)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.password_hasher = PasswordHasherService(
        max_concurrency=resolved_settings.password_hash_max_concurrency,
        queue_timeout_seconds=resolved_settings.password_hash_queue_timeout_seconds,
    )

    app.state.email_sender = SmtpEmailSender(resolved_settings)
    app.state.redis = create_redis_client(resolved_settings)
    app.state.rate_limiter = RateLimiter(
        app.state.redis, key_prefix=resolved_settings.rate_limit_key_prefix
    )

    # Ajouté en premier, donc exécuté à l'intérieur du contexte de requête : un refus 413 porte
    # lui aussi un identifiant de requête et le format RFC 9457.
    app.add_middleware(
        BodySizeLimitMiddleware, max_body_bytes=resolved_settings.max_request_body_bytes
    )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)

    # Toutes les routes métier sont versionnées : une évolution incompatible passera par
    # `/api/v2`, sans casser les clients existants ni laisser de route oubliée (OWASP API9).
    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(auth_router)
    api_v1.include_router(session_router)
    api_v1.include_router(tasks_router)
    api_v1.include_router(planning_router)
    app.include_router(api_v1)
    return app
