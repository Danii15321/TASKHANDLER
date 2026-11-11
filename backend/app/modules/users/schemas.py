"""Schémas du compte : lecture, changement de mot de passe, suppression et export."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.modules.auth.schemas import LoginPassword, NewPassword


class UserRead(BaseModel):
    """Vue publique d'un compte : liste blanche des champs exposés.

    `password_hash`, `token_version` et `is_active` sont absents : un champ ajouté à la table
    n'apparaît dans l'API que s'il est explicitement ajouté ici (OWASP API3).
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    email: str
    email_verified_at: datetime | None
    created_at: datetime


class PasswordChangeRequest(BaseModel):
    """Corps de `POST /users/me/password` : le mot de passe actuel prouve que c'est bien le
    titulaire, et pas seulement quelqu'un qui aurait volé un jeton d'accès."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    current_password: LoginPassword
    new_password: NewPassword


class AccountDeletionRequest(BaseModel):
    """Corps de `DELETE /users/me` : la suppression, irréversible, exige le mot de passe."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    password: LoginPassword


class ExportedTaskDay(BaseModel):
    """Jour d'une tâche, tel qu'exporté."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    day: date
    start_hour: int
    duration_hours: int
    status: str
    completed_at: datetime | None


class ExportedTask(BaseModel):
    """Tâche exportée, description comprise : ce sont les données de l'utilisateur."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    title: str
    description: str
    priority: str
    created_at: datetime
    updated_at: datetime
    days: list[ExportedTaskDay]


class ExportedSession(BaseModel):
    """Session exportée : ses dates, jamais l'empreinte du jeton."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revocation_reason: str | None


class AccountExport(BaseModel):
    """Export des données personnelles (RGPD, article 20 : droit à la portabilité)."""

    model_config = ConfigDict(extra="forbid")

    exported_at: datetime
    account: UserRead
    tasks: list[ExportedTask]
    sessions: list[ExportedSession]
