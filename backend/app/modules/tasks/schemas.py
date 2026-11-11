"""Schémas d'entrée et de sortie des tâches. Aucun ne porte `user_id` : il vient du jeton."""

import unicodedata
import uuid
from datetime import date, datetime
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)

from app.modules.tasks.models import (
    DESCRIPTION_MAX_LENGTH,
    FIRST_ALLOWED_DAY,
    HOURS_PER_DAY,
    LAST_ALLOWED_DAY,
    TITLE_MAX_LENGTH,
)

# Au plus 31 jours par requête, comme la période maximale de la grille.
MAX_DAYS_PER_REQUEST = 31
# Retour à la ligne, retour chariot et tabulation sont permis dans une description, pas ailleurs.
_DESCRIPTION_ALLOWED_CONTROLS = frozenset("\n\r\t")


# requires : `value` est une chaîne reçue d'un client.
# assigns  : rien (fonction pure).
# ensures  : renvoie le titre sans espaces autour ; lève `ValueError` s'il est vide après
#            nettoyage ou contient un caractère de contrôle, octet NUL compris.
def clean_title(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("le titre est vide")
    if any(unicodedata.category(character) == "Cc" for character in cleaned):
        raise ValueError("le titre contient des caractères de contrôle")
    return cleaned


# requires : `value` est une chaîne reçue d'un client.
# assigns  : rien (fonction pure).
# ensures  : renvoie la description sans espaces autour ; lève `ValueError` si elle est vide ou
#            contient un caractère de contrôle autre qu'un retour à la ligne ou une tabulation.
def clean_description(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("la description est vide")
    if any(
        unicodedata.category(character) == "Cc" and character not in _DESCRIPTION_ALLOWED_CONTROLS
        for character in cleaned
    ):
        raise ValueError("la description contient des caractères de contrôle")
    return cleaned


Title = Annotated[str, Field(max_length=TITLE_MAX_LENGTH), AfterValidator(clean_title)]
Description = Annotated[
    str, Field(max_length=DESCRIPTION_MAX_LENGTH), AfterValidator(clean_description)
]
# `StrictInt` : "9" ou 9.0 sont refusés ; un entier JSON est attendu, sans conversion implicite.
StartHour = Annotated[StrictInt, Field(ge=0, le=HOURS_PER_DAY - 1)]
DurationHours = Annotated[StrictInt, Field(ge=1, le=HOURS_PER_DAY)]
Day = Annotated[date, Field(ge=FIRST_ALLOWED_DAY, le=LAST_ALLOWED_DAY)]


class TaskDaySlot(BaseModel):
    """Créneau d'une tâche dans une journée : heure de début et durée, en heures entières."""

    model_config = ConfigDict(extra="forbid")

    start_hour: StartHour
    duration_hours: DurationHours

    # requires : les deux champs ont passé leur validation individuelle.
    # assigns  : rien.
    # ensures  : renvoie le créneau s'il se termine au plus tard à minuit ; sinon lève `ValueError`.
    @model_validator(mode="after")
    def _fits_in_day(self) -> Self:
        if self.start_hour + self.duration_hours > HOURS_PER_DAY:
            raise ValueError("le créneau dépasse minuit")
        return self


class TaskDayCreate(TaskDaySlot):
    """Jour d'une tâche, donné à sa création."""

    day: Day


class TaskCreate(BaseModel):
    """Corps de `POST /tasks` : titre, description et de 1 à 31 jours distincts."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    description: Description
    days: list[TaskDayCreate] = Field(min_length=1, max_length=MAX_DAYS_PER_REQUEST)

    # requires : chaque jour a passé sa validation.
    # assigns  : rien.
    # ensures  : renvoie la demande si aucun jour n'est répété ; sinon lève `ValueError`.
    @model_validator(mode="after")
    def _distinct_days(self) -> Self:
        if len({entry.day for entry in self.days}) != len(self.days):
            raise ValueError("un même jour apparaît plusieurs fois")
        return self


class TaskUpdate(BaseModel):
    """Corps de `PATCH /tasks/{id}` : seuls les champs présents sont modifiés."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None

    # requires : les champs présents ont passé leur validation.
    # assigns  : rien.
    # ensures  : renvoie la demande si elle modifie au moins un champ, sans jamais mettre un champ
    #            obligatoire à `null` ; sinon lève `ValueError`.
    @model_validator(mode="after")
    def _changes_something(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("aucun champ à modifier")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("un champ obligatoire ne peut pas être vidé")
        return self


class TaskDayRead(BaseModel):
    """Vue publique d'un jour de tâche."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    day: date
    start_hour: int
    duration_hours: int


class TaskRead(BaseModel):
    """Vue publique d'une tâche, avec ses jours. `user_id` n'y figure pas : c'est le demandeur."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    title: str
    description: str
    days: list[TaskDayRead]
    created_at: datetime
    updated_at: datetime
