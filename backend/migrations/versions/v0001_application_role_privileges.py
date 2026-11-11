"""Accorde au rôle applicatif les seuls droits de manipulation des données.

Révision : 0001
Précédente : aucune
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# requires : le rôle `taskhandler_app` existe et la migration utilise le rôle de migration.
# assigns  : les droits sur le schéma `public` et les privilèges par défaut du rôle de migration.
# ensures  : le rôle applicatif peut utiliser le schéma et manipuler les données des futures
#            tables, sans pouvoir modifier leur structure ; les autres rôles n'ont aucun accès.
def upgrade() -> None:
    # Par défaut, tout rôle peut parcourir `public` : on repart d'un accès explicite.
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA public TO taskhandler_app")
    # Les privilèges par défaut valent pour les tables créées ensuite par ce rôle : aucune
    # migration future n'a à répéter ces droits, ni ne peut les oublier. `TRUNCATE` est exclu.
    # `alembic_version`, créée avant cette migration, reste donc inaccessible à l'application.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO taskhandler_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO taskhandler_app"
    )


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : les droits sur le schéma `public` et les privilèges par défaut du rôle de migration.
# ensures  : les droits du rôle applicatif sont retirés et ceux de PostgreSQL rétablis.
def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM taskhandler_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM taskhandler_app"
    )
    op.execute("REVOKE USAGE ON SCHEMA public FROM taskhandler_app")
    op.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
