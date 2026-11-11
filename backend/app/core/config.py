"""Configuration de l'application, validée une seule fois au démarrage."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

Environment = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Chemins absolus : les fichiers sont trouvés quel que soit le dossier de lancement.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = _BACKEND_ROOT / ".env"
MIGRATION_ENV_FILE = _BACKEND_ROOT / ".env.migration"

# Liste autorisée plutôt qu'interdite : tout nouvel environnement reste sans documentation
# publique tant qu'il n'a pas été explicitement ajouté ici.
_ENVIRONMENTS_WITH_API_DOCS: frozenset[Environment] = frozenset({"development", "test"})

# Même logique pour le chiffrement : seuls ces environnements locaux peuvent s'en passer.
_ENVIRONMENTS_WITHOUT_DATABASE_TLS: frozenset[Environment] = frozenset({"development", "test"})

# Les rôles sont imposés : l'application ne peut pas démarrer par erreur avec le rôle de
# migration ou avec un superutilisateur, qui annuleraient le moindre privilège.
APP_DATABASE_ROLE = "taskhandler_app"
MIGRATION_DATABASE_ROLE = "taskhandler_migrator"

_DATABASE_DRIVER = "postgresql+asyncpg"
_MIN_DATABASE_PASSWORD_LENGTH = 24
_PLACEHOLDER_MARKERS = (
    "change",
    "dummy",
    "example",
    "exemple",
    "factice",
    "motdepasse",
    "password",
    "placeholder",
    "remplacer",
)


# requires : `url` est l'URL brute à contrôler, `expected_role` le seul rôle accepté.
# assigns  : rien (fonction pure).
# ensures  : ne renvoie rien si l'URL est sûre ; lève `ValueError` sinon. Le message d'erreur
#            ne contient jamais l'URL, car il peut finir dans les journaux de démarrage.
def _validate_database_url(url: SecretStr, *, expected_role: str, require_tls: bool) -> None:
    try:
        parsed_url = make_url(url.get_secret_value())
    except ArgumentError:
        # `from None` : l'exception d'origine pourrait contenir l'URL, donc le mot de passe.
        raise ValueError("URL de base de données illisible") from None

    if parsed_url.drivername != _DATABASE_DRIVER:
        raise ValueError(f"le pilote doit être {_DATABASE_DRIVER}")
    if parsed_url.username != expected_role:
        raise ValueError(f"le rôle de connexion doit être {expected_role}")
    if not parsed_url.host or not parsed_url.database:
        raise ValueError("l'hôte et le nom de la base sont obligatoires")

    password = parsed_url.password
    if not isinstance(password, str) or len(password) < _MIN_DATABASE_PASSWORD_LENGTH:
        raise ValueError(
            f"mot de passe absent ou de moins de {_MIN_DATABASE_PASSWORD_LENGTH} signes"
        )
    normalized_password = password.lower()
    if any(marker in normalized_password for marker in _PLACEHOLDER_MARKERS):
        raise ValueError("le mot de passe de la base correspond à une valeur d'exemple")

    # `verify-full` contrôle le certificat et le nom d'hôte : `require` chiffrerait la connexion
    # sans empêcher un intermédiaire de se faire passer pour le serveur.
    if require_tls and parsed_url.query.get("ssl") != "verify-full":
        raise ValueError("ssl=verify-full est obligatoire dans cet environnement")


class Settings(BaseSettings):
    """Paramètres lus dans l'environnement ; une valeur invalide empêche le démarrage."""

    model_config = SettingsConfigDict(
        env_prefix="TASKHANDLER_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # Une clé inconnue dans `.env` est souvent une faute de frappe : mieux vaut refuser de
        # démarrer que d'ignorer sans bruit un réglage de sécurité.
        extra="forbid",
        # La configuration ne doit pas pouvoir être modifiée pendant l'exécution.
        frozen=True,
        # Une erreur de validation ne doit jamais recopier la valeur reçue : ce message finit
        # dans les journaux de démarrage, et cette valeur pourrait être un secret.
        hide_input_in_errors=True,
    )

    # Valeur par défaut volontairement la plus stricte : oublier cette variable sur un serveur
    # ne doit pas exposer la documentation de l'API.
    environment: Environment = "production"
    log_level: LogLevel = "INFO"

    # Aucune valeur par défaut : sans secret, l'application refuse de démarrer. `SecretStr`
    # masque la valeur dans les `repr` et les journaux.
    database_url: SecretStr

    # Chaque ressource est bornée : une requête lente ou un afflux de connexions ne doit pas
    # pouvoir immobiliser le serveur PostgreSQL (OWASP API4).
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=50)
    database_pool_timeout_seconds: int = Field(default=5, ge=1, le=30)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=5_000, ge=100, le=60_000)
    database_lock_timeout_ms: int = Field(default=2_000, ge=100, le=60_000)
    database_idle_transaction_timeout_ms: int = Field(default=10_000, ge=100, le=600_000)

    # requires : les champs ont passé leur validation individuelle.
    # assigns  : rien.
    # ensures  : renvoie la configuration si l'URL est sûre ; sinon lève `ValueError`.
    @model_validator(mode="after")
    def _check_database_url(self) -> Self:
        _validate_database_url(
            self.database_url,
            expected_role=APP_DATABASE_ROLE,
            require_tls=self.environment not in _ENVIRONMENTS_WITHOUT_DATABASE_TLS,
        )
        return self

    # requires : rien.
    # assigns  : rien (lecture seule).
    # ensures  : renvoie `True` seulement dans un environnement explicitement autorisé.
    @property
    def api_docs_enabled(self) -> bool:
        return self.environment in _ENVIRONMENTS_WITH_API_DOCS


class MigrationSettings(BaseSettings):
    """Paramètres d'Alembic, séparés pour que l'API ne détienne jamais le secret de migration."""

    model_config = SettingsConfigDict(
        env_prefix="TASKHANDLER_MIGRATION_",
        env_file=MIGRATION_ENV_FILE,
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    environment: Environment = "production"
    database_url: SecretStr

    # requires : les champs ont passé leur validation individuelle.
    # assigns  : rien.
    # ensures  : renvoie la configuration si l'URL est sûre ; sinon lève `ValueError`.
    @model_validator(mode="after")
    def _check_database_url(self) -> Self:
        _validate_database_url(
            self.database_url,
            expected_role=MIGRATION_DATABASE_ROLE,
            require_tls=self.environment not in _ENVIRONMENTS_WITHOUT_DATABASE_TLS,
        )
        return self
