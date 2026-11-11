"""Crée la table `one_time_tokens` et ajoute la révocation par réinitialisation du mot de passe.

Révision : 0004
Précédente : 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASONS_BEFORE = "('rotated', 'logout', 'logout_all', 'reuse_detected')"
_REASONS_AFTER = "('rotated', 'logout', 'logout_all', 'reuse_detected', 'password_reset')"


# requires : la révision 0003 est appliquée, avec le rôle de migration.
# assigns  : crée `one_time_tokens` ; élargit la liste des raisons de révocation des sessions.
# ensures  : les jetons d'e-mail peuvent être stockés, et une réinitialisation peut couper les
#            sessions avec sa propre raison, distincte d'une déconnexion volontaire.
def upgrade() -> None:
    # `op.f()` : ces noms sont définitifs, la convention de nommage ne doit pas les préfixer.
    op.create_table(
        "one_time_tokens",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('email_verification', 'password_reset')",
            name=op.f("ck_one_time_tokens_purpose_known"),
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_one_time_tokens_token_hash_sha256_hex")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_one_time_tokens_expires_after_creation")
        ),
        sa.CheckConstraint(
            "purpose <> 'password_reset' OR expires_at <= created_at + interval '30 minutes'",
            name=op.f("ck_one_time_tokens_password_reset_max_lifetime"),
        ),
        sa.CheckConstraint(
            "purpose <> 'email_verification' OR expires_at <= created_at + interval '72 hours'",
            name=op.f("ck_one_time_tokens_email_verification_max_lifetime"),
        ),
        sa.CheckConstraint(
            "used_at IS NULL OR used_at >= created_at",
            name=op.f("ck_one_time_tokens_used_after_creation"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_one_time_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_one_time_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_one_time_tokens_token_hash")),
    )
    op.create_index(op.f("ix_one_time_tokens_user_id"), "one_time_tokens", ["user_id"])

    # Une contrainte `CHECK` ne se modifie pas : on la supprime puis on la recrée, dans la même
    # transaction, ce qui ne laisse aucun instant sans contrôle.
    op.drop_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"), "refresh_tokens", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"),
        "refresh_tokens",
        f"revocation_reason IN {_REASONS_AFTER}",
    )


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : supprime `one_time_tokens` ; ramène les révocations `password_reset` à `logout_all`.
# ensures  : le schéma revient exactement à l'état de la révision 0003, sans ligne qui violerait
#            l'ancienne contrainte.
def downgrade() -> None:
    # Sans cette mise à jour, recréer l'ancienne contrainte échouerait sur ces lignes.
    op.execute(
        "UPDATE refresh_tokens SET revocation_reason = 'logout_all' "
        "WHERE revocation_reason = 'password_reset'"
    )
    op.drop_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"), "refresh_tokens", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"),
        "refresh_tokens",
        f"revocation_reason IN {_REASONS_BEFORE}",
    )
    op.drop_index(op.f("ix_one_time_tokens_user_id"), table_name="one_time_tokens")
    op.drop_table("one_time_tokens")
