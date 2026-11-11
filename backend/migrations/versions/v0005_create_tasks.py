"""Crée les tables `tasks` et `task_days` (association N-N entre tâches et jours).

Révision : 0005
Précédente : 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# requires : la révision 0004 est appliquée, avec le rôle de migration.
# assigns  : crée `tasks`, `task_days` et leurs index.
# ensures  : les deux tables existent ; le rôle applicatif y lit et écrit des lignes grâce aux
#            privilèges par défaut de 0001, sans pouvoir modifier leur structure.
def upgrade() -> None:
    # `op.f()` : ces noms sont définitifs, la convention de nommage ne doit pas les préfixer.
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200", name=op.f("ck_tasks_title_length")
        ),
        sa.CheckConstraint("title ~ '\\S'", name=op.f("ck_tasks_title_not_blank")),
        sa.CheckConstraint(
            "title !~ '[[:cntrl:]]'", name=op.f("ck_tasks_title_no_control_characters")
        ),
        sa.CheckConstraint(
            "char_length(description) BETWEEN 1 AND 5000",
            name=op.f("ck_tasks_description_length"),
        ),
        sa.CheckConstraint("description ~ '\\S'", name=op.f("ck_tasks_description_not_blank")),
        sa.CheckConstraint(
            "description !~ '[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]'",
            name=op.f("ck_tasks_description_no_control_characters"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_tasks_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_tasks_id_user_id")),
    )
    op.create_index(op.f("ix_tasks_user_id"), "tasks", ["user_id"])

    op.create_table(
        "task_days",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("start_hour", sa.SmallInteger(), nullable=False),
        sa.Column("duration_hours", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "start_hour BETWEEN 0 AND 23", name=op.f("ck_task_days_start_hour_range")
        ),
        sa.CheckConstraint("duration_hours >= 1", name=op.f("ck_task_days_duration_positive")),
        sa.CheckConstraint(
            "start_hour + duration_hours <= 24", name=op.f("ck_task_days_fits_in_day")
        ),
        sa.CheckConstraint(
            "day BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'",
            name=op.f("ck_task_days_day_range"),
        ),
        # Clé étrangère composite : le couple (tâche, propriétaire) doit exister dans `tasks`.
        # Un jour ne peut donc jamais être rattaché à la tâche d'un autre utilisateur.
        sa.ForeignKeyConstraint(
            ["task_id", "user_id"],
            ["tasks.id", "tasks.user_id"],
            name=op.f("fk_task_days_task_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "day", name=op.f("pk_task_days")),
    )


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : supprime `task_days` puis `tasks`, donc toutes les tâches.
# ensures  : le schéma revient exactement à l'état de la révision 0004.
def downgrade() -> None:
    op.drop_table("task_days")
    op.drop_index(op.f("ix_tasks_user_id"), table_name="tasks")
    op.drop_table("tasks")
