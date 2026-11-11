"""Sécurité au niveau des lignes sur les tâches, et révocation par changement de mot de passe.

Révision : 0007
Précédente : 0006
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASONS_BEFORE = "('rotated', 'logout', 'logout_all', 'reuse_detected', 'password_reset')"
_REASONS_AFTER = (
    "('rotated', 'logout', 'logout_all', 'reuse_detected', 'password_reset', 'password_change')"
)


# requires : la révision 0006 est appliquée, avec le rôle de migration (propriétaire des tables).
# assigns  : active la RLS et crée une politique par table de tâches ; élargit les raisons de
#            révocation des sessions.
# ensures  : pour le rôle applicatif, `tasks` et `task_days` ne montrent et n'acceptent que les
#            lignes de l'utilisateur déclaré dans `app.current_user_id`. Sans utilisateur déclaré,
#            aucune ligne. Le rôle de migration, propriétaire, n'est pas concerné.
def upgrade() -> None:
    # `NULLIF(…, '')` : une fois la transaction terminée, le réglage revient à la chaîne vide, et
    # `''::uuid` lèverait une erreur ; `NULL` ne correspond simplement à aucune ligne.
    op.execute("ALTER TABLE tasks ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tasks_owner ON tasks FOR ALL TO taskhandler_app "
        "USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid) "
        "WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)"
    )
    op.execute("ALTER TABLE task_days ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY task_days_owner ON task_days FOR ALL TO taskhandler_app "
        "USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid) "
        "WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)"
    )

    op.drop_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"), "refresh_tokens", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"),
        "refresh_tokens",
        f"revocation_reason IN {_REASONS_AFTER}",
    )


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : supprime les politiques et désactive la RLS ; ramène les révocations
#            `password_change` à `logout_all`.
# ensures  : le schéma revient exactement à l'état de la révision 0006.
def downgrade() -> None:
    op.execute(
        "UPDATE refresh_tokens SET revocation_reason = 'logout_all' "
        "WHERE revocation_reason = 'password_change'"
    )
    op.drop_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"), "refresh_tokens", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_refresh_tokens_revocation_reason_known"),
        "refresh_tokens",
        f"revocation_reason IN {_REASONS_BEFORE}",
    )
    op.execute("DROP POLICY task_days_owner ON task_days")
    op.execute("ALTER TABLE task_days DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY tasks_owner ON tasks")
    op.execute("ALTER TABLE tasks DISABLE ROW LEVEL SECURITY")
