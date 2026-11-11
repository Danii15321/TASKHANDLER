"""Table `users` : chaque limite de validation est doublée d'une contrainte en base."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Nommé ici car le service le reconnaît pour répondre 409 au lieu d'une erreur 500.
EMAIL_UNIQUE_INDEX = "uq_users_email_lower"


class User(Base):
    """Compte utilisateur. Ce modèle n'est jamais renvoyé tel quel par l'API."""

    __tablename__ = "users"

    # UUID aléatoire plutôt qu'un entier : un identifiant ne révèle ni le nombre de comptes, ni
    # l'ordre de création, et ne se devine pas.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Incrémenté à chaque changement de mot de passe : il invalidera les jetons (phase 5).
    token_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        # Unicité sur `lower(email)` : même si un chemin de code oubliait de normaliser,
        # `Alice@…` et `alice@…` ne pourraient jamais coexister.
        Index(EMAIL_UNIQUE_INDEX, func.lower(email), unique=True),
        CheckConstraint("char_length(email) BETWEEN 3 AND 254", name="email_length"),
        CheckConstraint("email !~ '[[:cntrl:]]'", name="email_no_control_characters"),
        # Aucune autre forme d'empreinte n'est acceptée : un mot de passe en clair, en MD5 ou en
        # bcrypt ne peut pas être enregistré, même par erreur.
        CheckConstraint("left(password_hash, 10) = '$argon2id$'", name="password_hash_argon2id"),
        CheckConstraint("token_version >= 0", name="token_version_non_negative"),
    )
