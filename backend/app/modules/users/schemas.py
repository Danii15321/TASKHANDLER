"""Schémas de sortie des comptes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserRead(BaseModel):
    """Vue publique d'un compte : liste blanche des champs exposés.

    `password_hash`, `token_version` et `is_active` sont absents : un champ ajouté à la table
    n'apparaît dans l'API que s'il est explicitement ajouté ici (OWASP API3).
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime
