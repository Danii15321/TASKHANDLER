"""Inventaire des routes et contrat OpenAPI (OWASP API5 et API9).

Une route ajoutée sans protection, ou hors du préfixe versionné, fait échouer ces tests.
"""

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.auth.browser_session import require_csrf_for_login, require_csrf_for_refresh
from app.modules.auth.dependencies import enforce_user_rate_limit, get_current_user
from tests.conftest import unreachable_database_url

# Copie du contrat lue par le frontend : ses schémas zod sont vérifiés contre ce fichier.
FRONTEND_CONTRACT = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "openapi.json"
)
# Régénère la copie au lieu de la comparer : `UPDATE_API_CONTRACT=1 uv run pytest -k contract`.
UPDATE_CONTRACT = os.environ.get("UPDATE_API_CONTRACT") == "1"

# Routes publiques, exactement celles listées dans le README. Toute autre exige un jeton.
PUBLIC_ROUTES = {
    ("GET", "/health/live"),
    ("GET", "/health/ready"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/verify-email"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/password/forgot"),
    ("POST", "/api/v1/auth/password/reset"),
}


# requires : `app` est assemblée.
# assigns  : rien.
# ensures  : renvoie chaque couple (méthode, chemin) servi par l'application, avec le contexte
#            effectif de la route (préfixes et dépendances des routeurs inclus).
def declared_routes(app: FastAPI) -> dict[tuple[str, str], RouteContext]:
    # FastAPI inclut les routeurs sans les recopier : `app.routes` ne contient que des enveloppes.
    # `iter_route_contexts` est l'itérateur public que FastAPI utilise pour bâtir son OpenAPI ;
    # il donne le chemin complet et les dépendances héritées des routeurs.
    return {
        (method, str(context.path)): context
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        for method in context.methods or ()
    }


# requires : `route` est le contexte d'une route de l'application.
# assigns  : rien.
# ensures  : renvoie toutes les fonctions de dépendance que la route exécute, à toute profondeur.
def dependency_calls(route: RouteContext) -> set[object]:
    calls: set[object] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        calls.add(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


# requires : `app` est assemblée.
# assigns  : rien.
# ensures  : critère API5 : chaque route hors de la liste publique exige un jeton ET applique la
#            limite par utilisateur ; aucune route publique n'a été oubliée dans la liste.
def test_every_non_public_route_is_authenticated(app: FastAPI) -> None:
    routes = declared_routes(app)

    assert routes.keys() >= PUBLIC_ROUTES
    for key, route in routes.items():
        if key in PUBLIC_ROUTES:
            continue
        calls = dependency_calls(route)
        assert get_current_user in calls, key
        assert enforce_user_rate_limit in calls, key


# requires : `app` est assemblée.
# assigns  : rien.
# ensures  : critère API9 : toute route métier est sous `/api/v1` ; seule la santé est en dehors.
def test_routes_are_versioned(app: FastAPI) -> None:
    for _, path in declared_routes(app):
        assert path.startswith(("/api/v1/", "/health/")), path


# requires : rien.
# assigns  : la configuration globale des journaux.
# ensures  : le contrat publié décrit les erreurs de validation au format RFC 9457 réellement
#            renvoyé, et non le format par défaut de FastAPI, qui recopie la valeur reçue.
def test_openapi_documents_problem_errors() -> None:
    settings = Settings.model_validate(
        {"environment": "development", "database_url": unreachable_database_url()}
    )
    schema = TestClient(create_app(settings)).get("/openapi.json").json()

    assert "HTTPValidationError" not in str(schema)
    assert "Problem" in schema["components"]["schemas"]
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if "422" in operation["responses"]:
                content = operation["responses"]["422"]["content"]
                assert list(content) == ["application/problem+json"]


# requires : rien.
# assigns  : la configuration globale des journaux.
# ensures  : le contrat publié contient exactement les routes déclarées, ni plus ni moins.
def test_openapi_matches_declared_routes(app: FastAPI) -> None:
    settings = Settings.model_validate(
        {"environment": "development", "database_url": unreachable_database_url()}
    )
    development_app = create_app(settings)
    schema = TestClient(development_app).get("/openapi.json").json()
    documented = {
        (method.upper(), path) for path, item in schema["paths"].items() for method in item
    }

    assert documented == set(declared_routes(app))


# requires : `app` est assemblée.
# assigns  : rien.
# ensures  : les routes qui posent ou lisent le cookie de session portent leur contrôle anti-CSRF ;
#            un contrôle retiré par erreur fait échouer ce test.
def test_cookie_routes_are_protected_against_csrf(app: FastAPI) -> None:
    routes = declared_routes(app)

    assert require_csrf_for_login in dependency_calls(routes[("POST", "/api/v1/auth/login")])
    assert require_csrf_for_refresh in dependency_calls(routes[("POST", "/api/v1/auth/refresh")])


# requires : rien.
# assigns  : la configuration globale des journaux.
# ensures  : le jeton de renouvellement n'apparaît plus dans aucun schéma publié, ni en entrée ni
#            en sortie, et le renouvellement n'accepte plus de corps.
def test_refresh_token_never_travels_in_a_body() -> None:
    settings = Settings.model_validate(
        {"environment": "development", "database_url": unreachable_database_url()}
    )
    schema = TestClient(create_app(settings)).get("/openapi.json").json()

    assert "refresh_token" not in str(schema["components"]["schemas"])
    assert "requestBody" not in schema["paths"]["/api/v1/auth/refresh"]["post"]


# requires : `app` est assemblée ; le dépôt contient `frontend/`.
# assigns  : réécrit `frontend/src/api/openapi.json` si `UPDATE_API_CONTRACT=1`.
# ensures  : la copie du contrat utilisée par le frontend est exactement celle de l'API. Toute
#            modification d'une route ou d'un schéma oblige à la régénérer, puis à faire passer
#            les tests de contrat du frontend : un écart ne peut pas passer inaperçu.
def test_frontend_contract_is_current(app: FastAPI) -> None:
    expected = json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    if UPDATE_CONTRACT:
        FRONTEND_CONTRACT.write_text(expected, encoding="utf-8")
    assert FRONTEND_CONTRACT.read_text(encoding="utf-8") == expected, (
        "contrat du frontend périmé : UPDATE_API_CONTRACT=1 uv run pytest -k contract"
    )
