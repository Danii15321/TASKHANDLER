"""Réponses d'erreur uniformes au format RFC 9457 (`application/problem+json`)."""

import logging
from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_context import get_request_id

logger = logging.getLogger("app.errors")

INTERNAL_ERROR_DETAIL = "Une erreur interne est survenue."
INVALID_REQUEST_DETAIL = "La requête est invalide."


class ProblemResponse(JSONResponse):
    """Réponse JSON portant le type de contenu défini par la RFC 9457."""

    media_type = "application/problem+json"


# requires : `status_code` est un code de statut HTTP.
# assigns  : rien (fonction pure).
# ensures  : renvoie le libellé standard du code, ou `Error` si le code est non standard.
def _status_title(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


# requires : `detail` ne contient aucune information interne (trace, SQL, nom de table).
# assigns  : rien (lit seulement l'identifiant de la requête en cours).
# ensures  : renvoie une réponse RFC 9457 portant l'identifiant de la requête.
def build_problem(
    status_code: int,
    detail: str,
    *,
    errors: list[dict[str, object]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> ProblemResponse:
    content: dict[str, object] = {
        "type": "about:blank",
        "title": _status_title(status_code),
        "status": status_code,
        "detail": detail,
        "request_id": get_request_id(),
    }
    if errors is not None:
        content["errors"] = errors
    return ProblemResponse(status_code=status_code, content=content, headers=headers)


# requires : `exc` a été levée pendant le traitement d'une requête.
# assigns  : une entrée de journal si le code est une erreur serveur.
# ensures  : renvoie un problème RFC 9457 qui conserve le code et les en-têtes de `exc`.
async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> ProblemResponse:
    if exc.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        # Le détail d'une erreur serveur peut décrire l'infrastructure : il reste dans les
        # journaux, retrouvable grâce à l'identifiant de requête, et n'atteint pas le client.
        logger.error(
            "http_server_error",
            extra={"status_code": exc.status_code, "reason": str(exc.detail)},
        )
        detail = INTERNAL_ERROR_DETAIL
    else:
        detail = str(exc.detail)
    return build_problem(exc.status_code, detail, headers=exc.headers)


# requires : `exc` provient de la validation d'une requête par FastAPI.
# assigns  : rien.
# ensures  : renvoie un problème 422 qui ne recopie jamais les valeurs reçues.
async def handle_validation_error(
    _request: Request, exc: RequestValidationError
) -> ProblemResponse:
    # La réponse par défaut de FastAPI renvoie aussi le champ `input`, qui peut contenir un mot
    # de passe. On construit donc la réponse à partir des seuls champs jugés sûrs.
    errors: list[dict[str, object]] = [
        {"loc": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return build_problem(
        HTTPStatus.UNPROCESSABLE_ENTITY.value,
        INVALID_REQUEST_DETAIL,
        errors=errors,
    )


# requires : `app` est une application FastAPI en cours d'assemblage.
# assigns  : les gestionnaires d'exceptions de `app`.
# ensures  : les erreurs HTTP et de validation sont renvoyées au format RFC 9457.
def register_exception_handlers(app: FastAPI) -> None:
    app.exception_handler(StarletteHTTPException)(handle_http_exception)
    app.exception_handler(RequestValidationError)(handle_validation_error)
