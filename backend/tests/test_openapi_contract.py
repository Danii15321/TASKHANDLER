"""Inventaire des routes et contrat OpenAPI (OWASP API5 et API9).

Une route ajoutée sans protection, ou hors du préfixe versionné, fait échouer ces tests.
"""

from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.auth.dependencies import enforce_user_rate_limit, get_current_user
from tests.conftest import unreachable_database_url

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
