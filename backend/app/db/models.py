"""Registre des modèles : `metadata` ne contient que les tables importées ici.

Alembic compare ce `metadata` au schéma (`alembic check`) : un modèle absent d'ici serait
invisible, et une migration oubliée passerait inaperçue.
"""

from app.db.base import Base
from app.modules.auth.models import OneTimeToken, RefreshToken
from app.modules.tasks.models import Task, TaskDay
from app.modules.users.models import User

metadata = Base.metadata

__all__ = ["OneTimeToken", "RefreshToken", "Task", "TaskDay", "User", "metadata"]
