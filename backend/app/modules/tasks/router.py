"""Routes des tâches. L'authentification et la limite par utilisateur sont déclarées une seule fois,
au niveau du routeur : aucune route de ce module ne peut en être dispensée (OWASP API5).
"""

import uuid
from datetime import date
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path

from app.db.session import SessionDependency
from app.modules.auth.dependencies import AUTHENTICATED_DEPENDENCIES, CurrentUserDependency
from app.modules.tasks import service
from app.modules.tasks.models import FIRST_ALLOWED_DAY, LAST_ALLOWED_DAY
from app.modules.tasks.schemas import (
    TaskCreate,
    TaskDayRead,
    TaskDaySlot,
    TaskRead,
    TaskUpdate,
)
from app.modules.tasks.service import LastDayError, TaskNotFoundError, TaskView

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=AUTHENTICATED_DEPENDENCIES)

# Même message pour une tâche inexistante et pour celle d'un autre utilisateur : `404`, jamais
# `403`, qui confirmerait qu'elle existe.
NOT_FOUND_DETAIL = "Ressource introuvable."
DayPath = Annotated[date, Path(ge=FIRST_ALLOWED_DAY, le=LAST_ALLOWED_DAY)]


# requires : rien.
# assigns  : rien.
# ensures  : renvoie l'erreur 404 unique des tâches.
def _not_found() -> HTTPException:
    return HTTPException(status_code=HTTPStatus.NOT_FOUND, detail=NOT_FOUND_DETAIL)


# requires : `view` vient du service.
# assigns  : rien.
# ensures  : renvoie la vue publique ; le modèle ORM ne quitte jamais cette fonction.
def _to_read(view: TaskView) -> TaskRead:
    return TaskRead(
        id=view.task.id,
        title=view.task.title,
        description=view.task.description,
        days=[TaskDayRead.model_validate(task_day) for task_day in view.days],
        created_at=view.task.created_at,
        updated_at=view.task.updated_at,
    )


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : crée une tâche et ses jours pour l'utilisateur du jeton.
# ensures  : renvoie 201 et la tâche ; 422 si la demande est invalide, contient un champ inconnu
#            (dont `user_id`), aucun jour ou plus de 31 jours.
@router.post("", status_code=HTTPStatus.CREATED, response_model=TaskRead)
async def create_task(
    payload: TaskCreate, current: CurrentUserDependency, session: SessionDependency
) -> TaskRead:
    return _to_read(await service.create_task(session, user_id=current.user.id, data=payload))


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : rien.
# ensures  : renvoie la tâche et ses jours, ou 404 si elle n'appartient pas à l'utilisateur.
@router.get("/{task_id}", response_model=TaskRead)
async def read_task(
    task_id: uuid.UUID, current: CurrentUserDependency, session: SessionDependency
) -> TaskRead:
    try:
        return _to_read(await service.read_task(session, user_id=current.user.id, task_id=task_id))
    except TaskNotFoundError:
        raise _not_found() from None


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : modifie le titre et/ou la description de la tâche.
# ensures  : renvoie la tâche modifiée ; 404 si elle n'appartient pas à l'utilisateur (rien n'est
#            modifié) ; 422 si la demande est vide, invalide ou contient un champ inconnu.
@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    current: CurrentUserDependency,
    session: SessionDependency,
) -> TaskRead:
    try:
        view = await service.update_task(
            session, user_id=current.user.id, task_id=task_id, data=payload
        )
    except TaskNotFoundError:
        raise _not_found() from None
    return _to_read(view)


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : supprime la tâche et ses jours.
# ensures  : renvoie 204 ; 404 si elle n'appartient pas à l'utilisateur (rien n'est supprimé).
@router.delete("/{task_id}", status_code=HTTPStatus.NO_CONTENT)
async def delete_task(
    task_id: uuid.UUID, current: CurrentUserDependency, session: SessionDependency
) -> None:
    try:
        await service.delete_task(session, user_id=current.user.id, task_id=task_id)
    except TaskNotFoundError:
        raise _not_found() from None


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : crée ou remplace le créneau de la tâche pour ce jour.
# ensures  : renvoie le jour enregistré ; 404 si la tâche n'appartient pas à l'utilisateur ; 422 si
#            le jour ou le créneau est invalide.
@router.put("/{task_id}/days/{day}", response_model=TaskDayRead)
async def put_task_day(
    task_id: uuid.UUID,
    day: DayPath,
    payload: TaskDaySlot,
    current: CurrentUserDependency,
    session: SessionDependency,
) -> TaskDayRead:
    try:
        task_day = await service.put_task_day(
            session, user_id=current.user.id, task_id=task_id, day=day, slot=payload
        )
    except TaskNotFoundError:
        raise _not_found() from None
    return TaskDayRead.model_validate(task_day)


# requires : un jeton d'accès valide (vérifié par le routeur).
# assigns  : retire ce jour de la tâche.
# ensures  : renvoie 204 ; 404 si la tâche ou le jour n'existe pas pour l'utilisateur ; 409 s'il
#            s'agit du dernier jour de la tâche (rien n'est supprimé).
@router.delete("/{task_id}/days/{day}", status_code=HTTPStatus.NO_CONTENT)
async def delete_task_day(
    task_id: uuid.UUID, day: DayPath, current: CurrentUserDependency, session: SessionDependency
) -> None:
    try:
        await service.remove_task_day(session, user_id=current.user.id, task_id=task_id, day=day)
    except TaskNotFoundError:
        raise _not_found() from None
    except LastDayError:
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail="Une tâche doit garder au moins un jour : supprimez plutôt la tâche.",
        ) from None
