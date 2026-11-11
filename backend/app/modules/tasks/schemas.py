"""Schémas d'entrée et de sortie des tâches. Aucun ne porte `user_id` : il vient du jeton."""

import re
import unicodedata
import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Self, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
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
from app.modules.tasks.pagination import decode_cursor

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
Priority = Literal["low", "medium", "high"]
Status = Literal["todo", "in_progress", "done", "cancelled"]
_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


# requires : `value` est la valeur brute reçue pour un jour.
# assigns  : rien (fonction pure).
# ensures  : renvoie `value` si c'est un jour au format ISO 8601 `AAAA-MM-JJ` (ou déjà une date) ;
#            sinon lève `ValueError`. Sans ce contrôle, Pydantic accepterait aussi un horodatage
#            Unix (`1700000000`), une forme que personne n'a voulu envoyer.
def require_iso_day(value: object) -> object:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and _ISO_DAY.fullmatch(value):
        return value
    raise ValueError("jour attendu au format AAAA-MM-JJ")


Day = Annotated[
    date, BeforeValidator(require_iso_day), Field(ge=FIRST_ALLOWED_DAY, le=LAST_ALLOWED_DAY)
]


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
    priority: Priority = "medium"
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
    priority: Priority | None = None

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
    status: str
    completed_at: datetime | None


class TaskRead(BaseModel):
    """Vue publique d'une tâche, avec ses jours. `user_id` n'y figure pas : c'est le demandeur."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    title: str
    description: str
    priority: str
    days: list[TaskDayRead]
    created_at: datetime
    updated_at: datetime


class TaskDayStatusUpdate(BaseModel):
    """Corps de `PATCH /tasks/{id}/days/{day}` : le nouveau statut, rien d'autre.

    `completed_at` n'en fait pas partie : il est fixé par le serveur seul.
    """

    model_config = ConfigDict(extra="forbid")

    status: Status


# Au plus 100 éléments par page, quel que soit le paramètre reçu (OWASP API4).
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


# requires : `value` est le curseur brut reçu.
# assigns  : rien (fonction pure).
# ensures  : renvoie le curseur s'il se décode ; sinon lève `ValueError`, d'où un `422`.
def _require_valid_cursor(value: str) -> str:
    decode_cursor(value)
    return value


class TaskListQuery(BaseModel):
    """Paramètres de `GET /tasks`. Un paramètre inconnu est refusé (`extra="forbid"`)."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    cursor: Annotated[str, Field(max_length=200), AfterValidator(_require_valid_cursor)] | None = (
        None
    )


class TaskSummary(BaseModel):
    """Élément de la liste des tâches : léger, sans description ni jours."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    title: str
    priority: str
    created_at: datetime
    updated_at: datetime


class TaskPage(BaseModel):
    """Une page de tâches ; `next_cursor` vaut `null` sur la dernière page."""

    model_config = ConfigDict(extra="forbid")

    items: list[TaskSummary]
    next_cursor: str | None


class PlanningQuery(BaseModel):
    """Paramètres de `GET /planning` : 31 jours au plus, et des filtres facultatifs."""

    model_config = ConfigDict(extra="forbid")

    start: Day
    end: Day
    status: list[Status] = Field(default_factory=list, max_length=len(get_args(Status)))
    priority: list[Priority] = Field(default_factory=list, max_length=len(get_args(Priority)))

    # requires : les deux jours ont passé leur validation.
    # assigns  : rien.
    # ensures  : renvoie la demande si la période est dans l'ordre et dure 31 jours au plus ;
    #            sinon lève `ValueError`.
    @model_validator(mode="after")
    def _bounded_period(self) -> Self:
        if self.end < self.start:
            raise ValueError("la fin de la période précède son début")
        if (self.end - self.start).days + 1 > MAX_DAYS_PER_REQUEST:
            raise ValueError(f"la période dépasse {MAX_DAYS_PER_REQUEST} jours")
        return self


class PlanningEntry(BaseModel):
    """Case de la grille : titre et position, **jamais** la description de la tâche."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    task_id: uuid.UUID
    title: str
    priority: str
    day: date
    start_hour: int
    duration_hours: int
    status: str


class PlanningResponse(BaseModel):
    """Contenu de la grille pour une période."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    entries: list[PlanningEntry]
