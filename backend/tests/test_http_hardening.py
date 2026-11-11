"""Tests du durcissement HTTP : en-têtes de sécurité, hôtes autorisés et CORS."""

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Environment, Settings
from app.main import create_app
from tests.conftest import unreachable_database_url

SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "cross-origin-resource-policy": "same-origin",
}
ORIGIN = "https://app.taskhandler.fr"


# requires : `environment` est valide ; `overrides` contient des réglages valides.
# assigns  : la configuration globale des journaux.
# ensures  : renvoie un client relié à une application dans cet environnement.
def client_for(environment: Environment, **overrides: object) -> TestClient:
    settings = Settings.model_validate(
        {
            "environment": environment,
            "database_url": unreachable_database_url("?ssl=verify-full"),
            **overrides,
        }
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


# requires : `path` et `status` décrivent une réponse de l'API, réussie ou en erreur.
# assigns  : ajoute à `app` une route qui échoue, pour le cas 500.
# ensures  : chaque réponse, erreurs comprises, porte tous les en-têtes de sécurité.
@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/health/live", HTTPStatus.OK),
        ("GET", "/inconnue", HTTPStatus.NOT_FOUND),
        ("POST", "/health/live", HTTPStatus.METHOD_NOT_ALLOWED),
        ("GET", "/api/v1/users/me", HTTPStatus.UNAUTHORIZED),
        ("POST", "/api/v1/auth/login", HTTPStatus.UNPROCESSABLE_ENTITY),
        ("GET", "/boom", HTTPStatus.INTERNAL_SERVER_ERROR),
    ],
)
def test_every_response_carries_security_headers(
    app: FastAPI, client: TestClient, method: str, path: str, status: int
) -> None:
    # requires : rien.
    # assigns  : rien.
    # ensures  : lève toujours une erreur imprévue.
    async def boom() -> None:
        raise RuntimeError("panne")

    app.add_api_route("/boom", boom)

    response = client.request(method, path, json={})

    assert response.status_code == status
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value, name


# requires : rien.
# assigns  : la configuration globale des journaux.
# ensures  : HSTS est envoyé en production seulement, où l'API n'est servie qu'en HTTPS.
def test_hsts_only_in_production() -> None:
    production = client_for("production").get("/health/live")
    development = client_for("development").get("/health/live")

    assert production.headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"
    assert "strict-transport-security" not in development.headers


# requires : rien.
# assigns  : la configuration globale des journaux.
# ensures  : en développement, la documentation reste utilisable (sa propre politique n'est pas
#            écrasée), alors que le schéma JSON garde les en-têtes stricts.
def test_documentation_keeps_working_in_development() -> None:
    client = client_for("development")

    docs = client.get("/docs")
    schema = client.get("/openapi.json")

    assert docs.status_code == HTTPStatus.OK
    assert "content-security-policy" not in docs.headers
    assert schema.headers["content-security-policy"].startswith("default-src 'none'")


# requires : rien.
# assigns  : rien.
# ensures  : un en-tête `Host` hors de la liste est refusé : l'API ne répond pas pour un domaine
#            qui n'est pas le sien.
def test_unknown_host_is_refused(client: TestClient) -> None:
    response = client.get("/health/live", headers={"Host": "attaquant.exemple.org"})

    assert response.status_code == HTTPStatus.BAD_REQUEST


# requires : rien.
# assigns  : la configuration globale des journaux.
# ensures  : sans origine configurée, aucune réponse CORS n'est faite : CORS reste fermé.
def test_cors_is_closed_by_default(client: TestClient) -> None:
    response = client.options(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )

    assert "access-control-allow-origin" not in response.headers


# requires : rien.
# assigns  : la configuration globale des journaux.
# ensures  : seule l'origine listée est autorisée, sans cookies ; une autre origine ne reçoit rien.
def test_cors_allows_only_listed_origins() -> None:
    client = client_for("test", cors_allowed_origins=[ORIGIN])
    preflight = {"Access-Control-Request-Method": "POST"}

    allowed = client.options("/api/v1/auth/login", headers={"Origin": ORIGIN, **preflight})
    other = client.options(
        "/api/v1/auth/login", headers={"Origin": "https://attaquant.exemple.org", **preflight}
    )

    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in other.headers


# requires : `overrides` décrit une exposition HTTP dangereuse.
# assigns  : rien.
# ensures  : un hôte `*`, une origine `*`, une origine avec chemin, ou une origine en HTTP en
#            production empêchent le démarrage.
@pytest.mark.parametrize(
    ("environment", "overrides"),
    [
        ("test", {"allowed_hosts": ["*"]}),
        ("test", {"allowed_hosts": ["exemple.org/chemin"]}),
        ("test", {"cors_allowed_origins": ["*"]}),
        ("test", {"cors_allowed_origins": ["https://app.taskhandler.fr/chemin"]}),
        ("production", {"cors_allowed_origins": ["http://app.taskhandler.fr"]}),
    ],
)
def test_unsafe_http_exposure_is_refused(environment: str, overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "environment": environment,
                "database_url": unreachable_database_url("?ssl=verify-full"),
                **overrides,
            }
        )
