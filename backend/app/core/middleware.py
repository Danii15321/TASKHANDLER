"""Middleware qui donne un contexte à chaque requête et capture les erreurs imprévues."""

import logging
from http import HTTPStatus
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import INTERNAL_ERROR_DETAIL, build_problem
from app.core.request_context import bind_request_id, reset_request_id

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger("app.request")


class RequestContextMiddleware:
    """Attribue un identifiant à la requête, la journalise et masque les erreurs imprévues.

    C'est un middleware ASGI pur : `BaseHTTPMiddleware` exécute l'application dans une autre
    tâche, ce qui complique la propagation du contexte. La capture des exceptions est faite
    ici, car le gestionnaire de 500 de Starlette s'exécute à l'extérieur des middlewares :
    sa réponse n'aurait plus accès à l'identifiant de requête.
    """

    # requires : `app` est l'application ASGI à envelopper.
    # assigns  : `self.app`.
    # ensures  : le middleware est prêt à traiter des requêtes.
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    # requires : `scope`, `receive` et `send` respectent le protocole ASGI.
    # assigns  : l'identifiant de requête du contexte, une entrée de journal par requête.
    # ensures  : la réponse porte `X-Request-ID` ; une erreur imprévue devient un 500 générique,
    #            sauf si la réponse est déjà commencée : l'exception est alors relancée.
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # L'identifiant est toujours généré ici. Accepter celui du client lui permettrait
        # d'injecter du contenu dans les journaux ou de brouiller les corrélations.
        request_id = uuid4().hex
        context_token = bind_request_id(request_id)
        started_at = perf_counter()
        status_code = HTTPStatus.INTERNAL_SERVER_ERROR.value
        response_started = False

        # requires : `message` est un message ASGI émis par l'application.
        # assigns  : `status_code` et `response_started` de la requête en cours.
        # ensures  : le message est transmis, avec `X-Request-ID` s'il ouvre la réponse.
        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            # La trace complète reste côté serveur, reliée à la réponse par l'identifiant.
            logger.exception("unhandled_exception")
            if response_started:
                # Impossible de remplacer une réponse commencée : on laisse le serveur
                # interrompre la connexion plutôt que d'envoyer un contenu incohérent.
                raise
            response = build_problem(HTTPStatus.INTERNAL_SERVER_ERROR.value, INTERNAL_ERROR_DETAIL)
            await response(scope, receive, send_with_request_id)
        finally:
            # Seul le chemin est journalisé : la chaîne de requête peut contenir des données
            # personnelles, et les corps de requête ne sont jamais écrits.
            logger.info(
                "http_request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started_at) * 1000, 2),
                },
            )
            reset_request_id(context_token)
