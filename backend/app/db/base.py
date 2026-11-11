"""Base déclarative commune à tous les modèles SQLAlchemy."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Sans convention, PostgreSQL choisit lui-même le nom des contraintes : une migration ne
# pourrait plus les retrouver de façon fiable pour les modifier ou les supprimer.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Classe mère des modèles ; le schéma réel n'évolue que par des migrations Alembic."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
