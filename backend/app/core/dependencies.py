"""Dépendances FastAPI transverses."""

from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings


# requires : l'application a été assemblée par `create_app`.
# assigns  : rien.
# ensures  : renvoie la configuration validée au démarrage, la même pour toutes les requêtes.
def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDependency = Annotated[Settings, Depends(get_settings)]
