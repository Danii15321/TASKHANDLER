"""${message}

Révision : ${up_revision}
Précédente : ${down_revision | comma,n}
"""

from collections.abc import Sequence

% if imports:
${imports}
% endif
from alembic import op

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


# requires : la révision précédente est appliquée, avec le rôle de migration.
# assigns  : à compléter avant toute relecture.
# ensures  : à compléter avant toute relecture.
def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


# requires : cette révision est appliquée, avec le rôle de migration.
# assigns  : à compléter avant toute relecture.
# ensures  : à compléter avant toute relecture.
def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
