"""Crée la table `refresh_tokens` des sessions.

Révision : 0003
Précédente : 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# requires : la révision 0002 est appliquée, avec le rôle de migration.
# assigns  : crée la table `refresh_tokens` et ses index.
# ensures  : la table existe ; le rôle applicatif y lit et écrit des lignes grâce aux privilèges
#            par défaut de 0001, sans pouvoir modifier sa structure.
def upgrade() -> None:
    # `op.f()` : ces noms sont définitifs, la convention de nommage ne doit pas les préfixer.
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=20), nullable=True),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_refresh_tokens_token_hash_sha256_hex")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_refresh_tokens_expires_after_creation")
        ),
        sa.CheckConstraint(
            "expires_at <= session_expires_at",
            name=op.f("ck_refresh_tokens_within_session_lifetime"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revocation_reason IS NULL)",
            name=op.f("ck_refresh_tokens_revocation_consistent"),
        ),
        sa.CheckConstraint(
            "revocation_reason IN ('rotated', 'logout', 'logout_all', 'reuse_detected')",
            name=op.f("ck_refresh_tokens_revocation_reason_known"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"])
    op.create_index(op.f("ix_refresh_tokens_family_id"), "refresh_tokens", ["family_id"])


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : supprime la table `refresh_tokens`, donc toutes les sessions en cours.
# ensures  : le schéma revient exactement à l'état de la révision 0002.
def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_family_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
