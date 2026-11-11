"""Contrats de réponse des sondes de santé."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class LivenessResponse(BaseModel):
    """Réponse minimale : une sonde publique ne doit rien révéler de l'infrastructure."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    """Réponse minimale : ni version, ni hôte, ni nom de base."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
