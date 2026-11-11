"""Limite de taille des corps de requête (OWASP API4)."""

from http import HTTPStatus

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import build_problem

TOO_LARGE_DETAIL = "Le corps de la requête est trop volumineux."


class BodySizeLimitMiddleware:
    """Refuse tout corps de requête plus grand que `max_body_bytes`.

    Deux contrôles, car un client peut mentir ou se taire :
    1. `Content-Length` annoncé trop grand : refus immédiat, sans rien lire ;
    2. corps envoyé par morceaux, sans `Content-Length` ou avec une valeur fausse : les octets
       sont comptés au fil de la lecture, et la lecture s'arrête dès que la limite est dépassée.
    """

    # requires : `max_body_bytes` ≥ 1.
    # assigns  : rien.
    # ensures  : le middleware enveloppe `app`.
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    # requires : les arguments respectent le protocole ASGI.
    # assigns  : rien, en dehors de la réponse envoyée.
    # ensures  : transmet la requête à l'application si son corps respecte la limite ; sinon
    #            répond 413 (ou 400 si `Content-Length` est illisible), au format RFC 9457.
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            if not declared.isdigit():
                await build_problem(HTTPStatus.BAD_REQUEST, "En-tête Content-Length invalide.")(
                    scope, receive, send
                )
                return
            if int(declared) > self._max_body_bytes:
                await build_problem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, TOO_LARGE_DETAIL)(
                    scope, receive, send
                )
                return

        received = 0

        # requires : appelée par l'application pour lire le corps.
        # assigns  : augmente le nombre d'octets reçus.
        # ensures  : renvoie le message suivant ; lève une erreur 413, que FastAPI transforme en
        #            réponse RFC 9457, dès que le total dépasse la limite.
        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_body_bytes:
                    raise HTTPException(
                        status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE, detail=TOO_LARGE_DETAIL
                    )
            return message

        await self._app(scope, limited_receive, send)
