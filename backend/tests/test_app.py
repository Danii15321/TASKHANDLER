"""Tests de l'application assemblée : santé, documentation, erreurs et contexte de requête."""

from http import HTTPStatus

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.types import Receive, Scope, Send

from app.core.config import Environment, Settings
from app.core.errors import INTERNAL_ERROR_DETAIL
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.main import create_app
from tests.conftest import LogReader, unreachable_database_url

PROBLEM_MEDIA_TYPE = "application/problem+json"
DOCUMENTATION_PATHS = ["/docs", "/redoc", "/openapi.json"]


class CredentialsPayload(BaseModel):
    """Corps utilisé pour vérifier qu'une erreur de validation ne recopie pas l'entrée."""

    password: int


# requires : `environment` est un environnement valide.
# assigns  : la configuration globale des journaux.
# ensures  : renvoie un client relié à une application dans cet environnement.
def client_for(environment: Environment) -> TestClient:
    # Le paramètre TLS n'est utile qu'en production, mais il est accepté partout.
    database_url = unreachable_database_url("?ssl=verify-full")
    return TestClient(
        create_app(Settings(environment=environment, database_url=database_url, _env_file=None))
    )


# requires : `client` est relié à une application assemblée.
# assigns  : rien.
# ensures  : la sonde publique répond sans rien révéler d'autre que son état.
def test_liveness_returns_ok(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}


# requires : `path` est un chemin de documentation.
# assigns  : la configuration globale des journaux.
# ensures  : la documentation n'existe pas en production.
@pytest.mark.parametrize("path", DOCUMENTATION_PATHS)
def test_documentation_is_disabled_in_production(path: str) -> None:
    assert client_for("production").get(path).status_code == HTTPStatus.NOT_FOUND


# requires : `path` est un chemin de documentation.
# assigns  : la configuration globale des journaux.
# ensures  : la documentation reste disponible en développement.
@pytest.mark.parametrize("path", DOCUMENTATION_PATHS)
def test_documentation_is_enabled_in_development(path: str) -> None:
    assert client_for("development").get(path).status_code == HTTPStatus.OK


# requires : `client` est relié à une application assemblée.
# assigns  : rien.
# ensures  : une route inconnue renvoie un problème RFC 9457 complet.
def test_unknown_route_returns_problem(client: TestClient) -> None:
    response = client.get("/inconnue")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body = response.json()
    assert body["status"] == HTTPStatus.NOT_FOUND
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["request_id"] == response.headers[REQUEST_ID_HEADER]


# requires : `client` est relié à une application assemblée.
# assigns  : rien.
# ensures  : les en-têtes de l'erreur d'origine sont conservés.
def test_method_not_allowed_keeps_allow_header(client: TestClient) -> None:
    response = client.post("/health/live")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.headers["allow"] == "GET"


# requires : `app` et `client` sont reliés entre eux.
# assigns  : ajoute une route de test à `app`.
# ensures  : une erreur de validation décrit le champ sans recopier la valeur reçue.
def test_validation_error_does_not_echo_input(app: FastAPI, client: TestClient) -> None:
    # requires : `payload` a été validé par FastAPI.
    # assigns  : rien.
    # ensures  : renvoie un corps vide (cette route ne doit jamais être atteinte ici).
    async def login(payload: CredentialsPayload) -> dict[str, str]:
        return {"received": str(payload.password)}

    app.add_api_route("/login", login, methods=["POST"])

    response = client.post("/login", json={"password": "mot-de-passe-secret"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert "mot-de-passe-secret" not in response.text
    assert response.json()["errors"][0]["loc"] == ["body", "password"]


# requires : `app`, `client` et `read_logs` sont reliés à la même application.
# assigns  : ajoute une route de test à `app`.
# ensures  : une erreur imprévue donne un message générique, relié au journal par son identifiant.
def test_unexpected_error_is_generic_and_correlated(
    app: FastAPI, client: TestClient, read_logs: LogReader
) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : lève toujours une erreur contenant des détails internes.
    async def explode() -> None:
        raise RuntimeError("SELECT * FROM users WHERE secret_table")

    app.add_api_route("/explode", explode)

    response = client.get("/explode")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body = response.json()
    assert body["detail"] == INTERNAL_ERROR_DETAIL
    for internal_detail in ("SELECT", "secret_table", "RuntimeError", "Traceback"):
        assert internal_detail not in response.text

    request_id = response.headers[REQUEST_ID_HEADER]
    assert body["request_id"] == request_id
    error_events = [event for event in read_logs() if event["message"] == "unhandled_exception"]
    assert len(error_events) == 1
    assert error_events[0]["request_id"] == request_id
    assert "RuntimeError" in str(error_events[0]["exception"])


# requires : `app` et `client` sont reliés entre eux.
# assigns  : ajoute une route de test à `app`.
# ensures  : le détail d'une erreur serveur explicite n'atteint jamais le client.
def test_server_http_exception_hides_detail(app: FastAPI, client: TestClient) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : lève toujours une erreur 503 décrivant l'infrastructure.
    async def unavailable() -> None:
        raise HTTPException(status_code=503, detail="postgres-interne:5432 injoignable")

    app.add_api_route("/unavailable", unavailable)

    response = client.get("/unavailable")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert "postgres-interne" not in response.text
    assert response.json()["detail"] == INTERNAL_ERROR_DETAIL


# requires : `client` est relié à une application assemblée.
# assigns  : rien.
# ensures  : un identifiant fourni par le client est ignoré.
def test_client_request_id_is_ignored(client: TestClient) -> None:
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: "valeur-du-client"})

    assert response.headers[REQUEST_ID_HEADER] != "valeur-du-client"


# requires : `client` est relié à une application assemblée.
# assigns  : rien.
# ensures  : deux requêtes reçoivent des identifiants différents.
def test_each_request_gets_its_own_id(client: TestClient) -> None:
    first = client.get("/health/live").headers[REQUEST_ID_HEADER]
    second = client.get("/health/live").headers[REQUEST_ID_HEADER]

    assert first != second


# requires : `client` et `read_logs` sont reliés à la même application.
# assigns  : rien.
# ensures  : le journal d'accès garde le chemin mais jamais la chaîne de requête.
def test_access_log_excludes_query_string(client: TestClient, read_logs: LogReader) -> None:
    client.get("/health/live?email=personne%40example.com")

    events = [event for event in read_logs() if event["message"] == "http_request"]
    assert events[0]["path"] == "/health/live"
    assert events[0]["status_code"] == HTTPStatus.OK
    assert "example.com" not in str(events)


# requires : rien.
# assigns  : rien.
# ensures  : une erreur après le début de la réponse est relancée, pas masquée par un faux 500.
def test_error_after_response_start_is_reraised() -> None:
    # requires : les arguments respectent le protocole ASGI.
    # assigns  : rien.
    # ensures  : commence une réponse, puis lève toujours une erreur.
    async def failing_app(_scope: Scope, _receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("erreur en cours de réponse")

    client = TestClient(RequestContextMiddleware(failing_app))

    with pytest.raises(RuntimeError):
        client.get("/")
