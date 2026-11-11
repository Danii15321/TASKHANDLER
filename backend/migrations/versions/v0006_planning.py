"""Grille : priorité, statut, interdiction des chevauchements et index des requêtes réelles.

Révision : 0006
Précédente : 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# requires : la révision 0005 est appliquée, avec le rôle de migration ; aucun créneau existant
#            ne chevauche un autre créneau du même utilisateur (sinon la migration échoue, et rien
#            n'est modifié : la transaction est annulée).
# assigns  : installe `btree_gist` ; ajoute priorité, statut et `completed_at` ; crée la contrainte
#            d'exclusion et les index.
# ensures  : deux créneaux non annulés d'un utilisateur ne peuvent plus se chevaucher, même entre
#            requêtes simultanées.
def upgrade() -> None:
    # `btree_gist` permet l'égalité sur `uuid` et `date` dans un index GiST, que la contrainte
    # d'exclusion combine avec le chevauchement d'intervalles. Extension « de confiance » : le rôle
    # de migration, propriétaire de la base, l'installe sans être superutilisateur.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.add_column(
        "tasks",
        sa.Column(
            "priority", sa.String(length=10), server_default=sa.text("'medium'"), nullable=False
        ),
    )
    op.create_check_constraint(
        op.f("ck_tasks_priority_known"), "tasks", "priority IN ('low', 'medium', 'high')"
    )
    op.add_column(
        "task_days",
        sa.Column("status", sa.String(length=15), server_default=sa.text("'todo'"), nullable=False),
    )
    op.add_column("task_days", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_task_days_status_known"),
        "task_days",
        "status IN ('todo', 'in_progress', 'done', 'cancelled')",
    )
    op.create_check_constraint(
        op.f("ck_task_days_completed_at_consistent"),
        "task_days",
        "(status = 'done') = (completed_at IS NOT NULL)",
    )
    # SQL littéral : `op.create_exclude_constraint` ne sait pas traiter l'expression `int4range`.
    # `[début, fin)` : 9 h à 10 h et 10 h à 11 h se touchent sans se chevaucher.
    op.execute(
        "ALTER TABLE task_days ADD CONSTRAINT ex_task_days_no_overlap "
        "EXCLUDE USING gist ("
        "user_id WITH =, day WITH =, int4range(start_hour, start_hour + duration_hours) WITH &&"
        ") WHERE (status <> 'cancelled')"
    )

    # Index tirés des deux requêtes réelles de la phase : la liste paginée des tâches et la grille.
    op.drop_index(op.f("ix_tasks_user_id"), table_name="tasks")
    op.create_index(
        op.f("ix_tasks_user_created_id"),
        "tasks",
        ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        op.f("ix_task_days_user_day_start"), "task_days", ["user_id", "day", "start_hour"]
    )


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : supprime index, contraintes et colonnes ajoutés, puis l'extension `btree_gist`.
# ensures  : le schéma revient exactement à l'état de la révision 0005 ; statuts et priorités
#            sont perdus.
def downgrade() -> None:
    op.drop_index(op.f("ix_task_days_user_day_start"), table_name="task_days")
    op.drop_index(op.f("ix_tasks_user_created_id"), table_name="tasks")
    op.create_index(op.f("ix_tasks_user_id"), "tasks", ["user_id"])
    op.execute("ALTER TABLE task_days DROP CONSTRAINT ex_task_days_no_overlap")
    op.drop_constraint(op.f("ck_task_days_completed_at_consistent"), "task_days", type_="check")
    op.drop_constraint(op.f("ck_task_days_status_known"), "task_days", type_="check")
    op.drop_column("task_days", "completed_at")
    op.drop_column("task_days", "status")
    op.drop_constraint(op.f("ck_tasks_priority_known"), "tasks", type_="check")
    op.drop_column("tasks", "priority")
    # Plus aucun objet n'en dépend : l'extension, installée par `upgrade`, est retirée.
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
