"""Contrat OpenAPI fidèle à l'API réelle : les erreurs y sont décrites en RFC 9457."""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

PROBLEM_MEDIA_TYPE = "application/problem+json"
_PROBLEM_SCHEMA_NAME = "Problem"
_PROBLEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Erreur au format RFC 9457. Ne contient jamais la valeur reçue.",
    "required": ["type", "title", "status", "request_id"],
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
        "request_id": {"type": "string"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "loc": {"type": "array", "items": {"type": ["string", "integer"]}},
                    "message": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
        },
    },
}


# requires : `app` est assemblée ; ses routes sont toutes déclarées.
# assigns  : remplace la génération du schéma OpenAPI de `app`.
# ensures  : chaque réponse 422 du contrat renvoie le schéma RFC 9457 réellement produit par l'API,
#            et non le format par défaut de FastAPI, qui contient la valeur reçue (`input`).
def install_problem_openapi(app: FastAPI) -> None:
    # requires : rien.
    # assigns  : met le schéma en cache dans `app.openapi_schema`, au premier appel.
    # ensures  : renvoie le schéma OpenAPI corrigé.
    def build() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        for default_name in ("HTTPValidationError", "ValidationError"):
            components.pop(default_name, None)
        components[_PROBLEM_SCHEMA_NAME] = _PROBLEM_SCHEMA
        reference = {"$ref": f"#/components/schemas/{_PROBLEM_SCHEMA_NAME}"}
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                responses = operation.get("responses", {})
                if "422" in responses:
                    responses["422"]["content"] = {PROBLEM_MEDIA_TYPE: {"schema": reference}}
        app.openapi_schema = schema
        return schema

    app.openapi = build  # type: ignore[method-assign]  # point d'extension documenté de FastAPI
