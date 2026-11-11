"""Table `refresh_tokens` : une ligne par jeton de renouvellement émis.

Une session (« famille ») regroupe tous les jetons nés d'une même connexion : chaque
renouvellement révoque le jeton utilisé et en ajoute un nouveau, avec le même `family_id`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Raisons de révocation. Seule `rotated` signale un vol quand le jeton est présenté de nouveau :
# un jeton révoqué par une déconnexion n'a rien de suspect.
REASON_ROTATED = "rotated"
REASON_LOGOUT = "logout"
REASON_LOGOUT_ALL = "logout_all"
REASON_REUSE_DETECTED = "reuse_detected"


class RefreshToken(Base):
    """Jeton de renouvellement. La valeur du jeton n'est jamais stockée, seulement son empreinte."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Suppression en cascade : supprimer un compte efface toutes ses sessions.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Limite absolue de la session, copiée de jeton en jeton : renouveler ne la repousse jamais.
    session_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(20))

    __table_args__ = (
        UniqueConstraint("token_hash"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256_hex"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        CheckConstraint("expires_at <= session_expires_at", name="within_session_lifetime"),
        # Une révocation a toujours une date et une raison, ou aucune des deux.
        CheckConstraint(
            "(revoked_at IS NULL) = (revocation_reason IS NULL)", name="revocation_consistent"
        ),
        CheckConstraint(
            "revocation_reason IN ('rotated', 'logout', 'logout_all', 'reuse_detected')",
            name="revocation_reason_known",
        ),
    )
