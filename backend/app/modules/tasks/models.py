"""Tables `tasks` et `task_days` : chaque limite de validation a sa contrainte en base."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Ces bornes sont répétées en toutes lettres dans les contraintes `CHECK` ci-dessous, sans f-string
# dans le SQL ; un test vérifie que la base et ces constantes restent d'accord.
TITLE_MAX_LENGTH = 200
DESCRIPTION_MAX_LENGTH = 5000
# Bornes des jours : au-delà, une date est presque sûrement une erreur de saisie ou une sonde.
FIRST_ALLOWED_DAY = date(2000, 1, 1)
LAST_ALLOWED_DAY = date(2100, 12, 31)
HOURS_PER_DAY = 24

# Listes fermées, répétées en toutes lettres dans les contraintes `CHECK`.
PRIORITIES = ("low", "medium", "high")
STATUSES = ("todo", "in_progress", "done", "cancelled")
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
# Nommée ici, car le service la reconnaît pour répondre 409 au lieu d'une erreur 500.
NO_OVERLAP_CONSTRAINT = "ex_task_days_no_overlap"


class Task(Base):
    """Tâche d'un utilisateur. Ce modèle n'est jamais renvoyé tel quel par l'API."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH))
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), server_default=text("'medium'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Cible de la clé étrangère composite de `task_days` : un jour ne peut désigner une tâche
        # qu'avec le bon propriétaire.
        UniqueConstraint("id", "user_id", name="uq_tasks_id_user_id"),
        # Index de la liste paginée : `WHERE user_id = … ORDER BY created_at DESC, id DESC`. Il
        # sert aussi la clé étrangère vers `users`, qu'il commence par `user_id`.
        Index("ix_tasks_user_created_id", "user_id", text("created_at DESC"), text("id DESC")),
        CheckConstraint("priority IN ('low', 'medium', 'high')", name="priority_known"),
        # Longueur, et au moins un caractère qui ne soit pas un blanc (`\\S`) : un champ fait
        # d'espaces, de retours à la ligne ou de tabulations est vide. `btrim` ne suffirait pas :
        # il ne retire que les espaces. Aucun caractère de contrôle dans le titre.
        CheckConstraint("char_length(title) BETWEEN 1 AND 200", name="title_length"),
        CheckConstraint("title ~ '\\S'", name="title_not_blank"),
        CheckConstraint("title !~ '[[:cntrl:]]'", name="title_no_control_characters"),
        CheckConstraint("char_length(description) BETWEEN 1 AND 5000", name="description_length"),
        CheckConstraint("description ~ '\\S'", name="description_not_blank"),
        # La description garde ses retours à la ligne et tabulations, mais aucun autre caractère
        # de contrôle (échappements de terminal…). L'octet NUL, lui, est refusé par PostgreSQL
        # dans tout texte.
        CheckConstraint(
            "description !~ '[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]'",
            name="description_no_control_characters",
        ),
    )


class TaskDay(Base):
    """Placement d'une tâche sur un jour : la table d'association N-N entre tâches et jours."""

    __tablename__ = "task_days"

    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # Copié depuis la tâche, et garanti identique par la clé étrangère composite : chaque requête
    # peut ainsi filtrer par propriétaire directement, sans jointure.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    start_hour: Mapped[int] = mapped_column(SmallInteger)
    duration_hours: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(15), server_default=text("'todo'"))
    # Renseigné par le serveur seul, au passage à `done` : jamais fourni par le client.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["tasks.id", "tasks.user_id"],
            name="fk_task_days_task_owner",
            ondelete="CASCADE",
        ),
        CheckConstraint("start_hour BETWEEN 0 AND 23", name="start_hour_range"),
        CheckConstraint("duration_hours >= 1", name="duration_positive"),
        # Une tâche tient dans sa journée : elle ne déborde jamais sur le jour suivant.
        CheckConstraint("start_hour + duration_hours <= 24", name="fits_in_day"),
        CheckConstraint("day BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'", name="day_range"),
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done', 'cancelled')", name="status_known"
        ),
        # `completed_at` existe si et seulement si le jour est fait.
        CheckConstraint(
            "(status = 'done') = (completed_at IS NOT NULL)", name="completed_at_consistent"
        ),
        # Aucun chevauchement entre deux créneaux non annulés d'un même utilisateur, le même jour.
        # `[début, fin)` : 9 h à 10 h et 10 h à 11 h se touchent sans se chevaucher. La base le
        # garantit, y compris entre deux requêtes simultanées, ce qu'aucun contrôle en Python
        # (lire, puis écrire) ne pourrait faire.
        ExcludeConstraint(
            ("user_id", "="),
            ("day", "="),
            (text("int4range(start_hour, start_hour + duration_hours)"), "&&"),
            name=NO_OVERLAP_CONSTRAINT,
            using="gist",
            where=text("status <> 'cancelled'"),
        ),
        # Index de la grille : `WHERE user_id = … AND day BETWEEN … ORDER BY day, start_hour`.
        Index("ix_task_days_user_day_start", "user_id", "day", "start_hour"),
    )
