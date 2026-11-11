"""Crée la table `users` et ses contraintes.

Révision : 0002
Précédente : 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# requires : la révision 0001 est appliquée, avec le rôle de migration.
# assigns  : crée la table `users` et son index unique.
# ensures  : la table existe ; grâce aux privilèges par défaut de 0001, le rôle applicatif peut
#            y lire et y écrire des lignes, sans pouvoir modifier sa structure.
def upgrade() -> None:
    # `op.f()` marque chaque nom comme définitif : sans lui, la convention de nommage le
    # préfixerait une seconde fois (`ck_users_ck_users_…`) et les migrations suivantes ne
    # retrouveraient plus ces contraintes.
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(email) BETWEEN 3 AND 254", name=op.f("ck_users_email_length")
        ),
        sa.CheckConstraint(
            "email !~ '[[:cntrl:]]'", name=op.f("ck_users_email_no_control_characters")
        ),
        sa.CheckConstraint(
            "left(password_hash, 10) = '$argon2id$'", name=op.f("ck_users_password_hash_argon2id")
        ),
        sa.CheckConstraint("token_version >= 0", name=op.f("ck_users_token_version_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("uq_users_email_lower"), "users", [sa.text("lower(email)")], unique=True)


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : supprime la table `users` et toutes ses lignes.
# ensures  : le schéma revient exactement à l'état de la révision 0001.
def downgrade() -> None:
    op.drop_index(op.f("uq_users_email_lower"), table_name="users")
    op.drop_table("users")
