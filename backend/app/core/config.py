"""Configuration de l'application, validée une seule fois au démarrage."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import unquote, urlsplit

from email_validator import EmailNotValidError, validate_email
from pydantic import AnyHttpUrl, Field, IPvAnyNetwork, SecretStr, model_validator
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
# Utilisateur Redis imposé : c'est le seul dont les droits ACL sont restreints au limiteur.
REDIS_USER = "taskhandler_app"

_DATABASE_DRIVER = "postgresql+asyncpg"
# 64 caractères hexadécimaux = 256 bits, le minimum exigé pour une clé HMAC-SHA256.
MIN_JWT_SECRET_LENGTH = 64
# Une clé longue mais faite de quelques caractères répétés (`aaaa…`) n'a presque aucune entropie.
_MIN_JWT_SECRET_DISTINCT_CHARACTERS = 12
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


# requires : `password` est le mot de passe extrait d'une URL de connexion, ou `None`.
# assigns  : rien (fonction pure).
# ensures  : ne renvoie rien si le mot de passe est assez long et n'est pas une valeur d'exemple ;
#            sinon lève `ValueError`, sans jamais recopier le mot de passe.
def _ensure_strong_password(password: str | None) -> None:
    if not isinstance(password, str) or len(password) < _MIN_DATABASE_PASSWORD_LENGTH:
        raise ValueError(
            f"mot de passe absent ou de moins de {_MIN_DATABASE_PASSWORD_LENGTH} signes"
        )
    normalized_password = password.lower()
    if any(marker in normalized_password for marker in _PLACEHOLDER_MARKERS):
        raise ValueError("le mot de passe correspond à une valeur d'exemple")


# requires : `url` est l'URL Redis brute à contrôler.
# assigns  : rien (fonction pure).
# ensures  : ne renvoie rien si l'URL est sûre ; lève `ValueError` sinon, sans recopier l'URL.
def _validate_redis_url(url: SecretStr, *, require_tls: bool) -> None:
    try:
        parts = urlsplit(url.get_secret_value())
        hostname, port = parts.hostname, parts.port
    except ValueError:
        raise ValueError("URL Redis illisible") from None

    if parts.scheme not in {"redis", "rediss"}:
        raise ValueError("le schéma doit être redis:// ou rediss://")
    # `rediss://` chiffre la connexion ; redis-py vérifie alors le certificat par défaut.
    if require_tls and parts.scheme != "rediss":
        raise ValueError("rediss:// est obligatoire dans cet environnement")
    if parts.username != REDIS_USER:
        raise ValueError(f"l'utilisateur Redis doit être {REDIS_USER}")
    if not hostname or port is None:
        raise ValueError("l'hôte et le port Redis sont obligatoires")
    _ensure_strong_password(unquote(parts.password) if parts.password is not None else None)


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

    _ensure_strong_password(parsed_url.password)

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

    # Chaque hachage argon2id réserve 64 Mio : 4 hachages simultanés plafonnent la mémoire à
    # 256 Mio. Au-delà, les demandes attendent leur tour, puis sont refusées (OWASP API4).
    password_hash_max_concurrency: int = Field(default=4, ge=1, le=32)
    password_hash_queue_timeout_seconds: int = Field(default=10, ge=1, le=60)

    # Clé de signature des jetons d'accès. Sans valeur par défaut : la changer invalide tous les
    # jetons d'accès, et sa fuite permettrait de fabriquer des jetons pour n'importe quel compte.
    jwt_secret: SecretStr
    # Une session expire après cette inactivité, et au plus tard après sa durée maximale.
    refresh_token_idle_days: int = Field(default=7, ge=1, le=30)
    session_max_days: int = Field(default=30, ge=1, le=90)

    # Redis porte les compteurs de limitation de débit. Sans valeur par défaut : c'est un secret.
    redis_url: SecretStr
    # Le préfixe doit rester dans `rl:*`, seul espace autorisé par les droits ACL de Redis.
    rate_limit_key_prefix: str = Field(default="rl:", pattern=r"^rl:[a-z0-9:]{0,40}$")
    # Seuls ces réseaux peuvent fixer l'IP du client via `X-Forwarded-For`. Vide par défaut :
    # sans proxy déclaré, l'en-tête est ignoré, car n'importe quel client peut l'écrire.
    trusted_proxies: tuple[IPvAnyNetwork, ...] = ()
    # Taille maximale d'un corps de requête, 1 Mio par défaut (OWASP API4).
    max_request_body_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)

    # Limites par fenêtre glissante, bornées pour qu'une erreur de réglage ne les supprime pas.
    login_ip_limit_per_minute: int = Field(default=20, ge=1, le=1000)
    login_account_limit_per_minute: int = Field(default=5, ge=1, le=100)
    register_ip_limit_per_hour: int = Field(default=5, ge=1, le=1000)
    register_email_limit_per_hour: int = Field(default=3, ge=1, le=100)
    refresh_user_limit_per_minute: int = Field(default=30, ge=1, le=1000)
    authenticated_user_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    forgot_password_ip_limit_per_hour: int = Field(default=5, ge=1, le=1000)
    forgot_password_email_limit_per_hour: int = Field(default=3, ge=1, le=100)
    verification_resend_limit_per_hour: int = Field(default=3, ge=1, le=100)

    # Envoi des e-mails. `tls` par défaut : le mode le plus strict quand rien n'est précisé.
    smtp_host: str = Field(min_length=1, max_length=253)
    smtp_port: int = Field(ge=1, le=65_535)
    smtp_security: Literal["none", "starttls", "tls"] = "tls"
    smtp_username: str | None = Field(default=None, max_length=256)
    smtp_password: SecretStr | None = None
    email_from: str = Field(max_length=254)
    # Adresse du frontend : les liens envoyés par e-mail pointent vers lui, jamais vers l'API.
    frontend_base_url: AnyHttpUrl
    # Durées de validité des liens. La réinitialisation ne dépasse jamais 30 minutes.
    email_verification_ttl_hours: int = Field(default=24, ge=1, le=72)
    password_reset_ttl_minutes: int = Field(default=30, ge=5, le=30)

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

    # requires : les champs ont passé leur validation individuelle.
    # assigns  : rien.
    # ensures  : renvoie la configuration si la clé, Redis et les durées de session sont sûrs ;
    #            sinon lève `ValueError`, sans recopier aucun secret.
    @model_validator(mode="after")
    def _check_sessions(self) -> Self:
        secret = self.jwt_secret.get_secret_value()
        if len(secret) < MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"la clé JWT doit contenir au moins {MIN_JWT_SECRET_LENGTH} caractères"
            )
        if len(set(secret)) < _MIN_JWT_SECRET_DISTINCT_CHARACTERS:
            raise ValueError("la clé JWT est trop répétitive pour être aléatoire")
        if any(marker in secret.lower() for marker in _PLACEHOLDER_MARKERS):
            raise ValueError("la clé JWT correspond à une valeur d'exemple")
        _validate_redis_url(
            self.redis_url,
            require_tls=self.environment not in _ENVIRONMENTS_WITHOUT_DATABASE_TLS,
        )
        if self.refresh_token_idle_days > self.session_max_days:
            raise ValueError("l'inactivité permise dépasse la durée maximale d'une session")
        return self

    # requires : les champs ont passé leur validation individuelle.
    # assigns  : rien.
    # ensures  : renvoie la configuration si l'envoi d'e-mails est sûr ; sinon lève `ValueError`.
    #            Hors développement, SMTP doit être chiffré et authentifié, et les liens en HTTPS.
    @model_validator(mode="after")
    def _check_email_delivery(self) -> Self:
        try:
            validate_email(self.email_from, check_deliverability=False)
        except EmailNotValidError:
            raise ValueError("adresse d'expédition invalide") from None
        # Les liens ajoutent `#token=…` à cette adresse : une requête ou un fragment déjà présents
        # produiraient un lien ambigu.
        if self.frontend_base_url.query or self.frontend_base_url.fragment:
            raise ValueError("l'adresse du frontend ne doit contenir ni requête ni fragment")
        if self.environment not in _ENVIRONMENTS_WITHOUT_DATABASE_TLS:
            if self.smtp_security == "none":
                raise ValueError("SMTP doit être chiffré dans cet environnement")
            if not self.smtp_username or self.smtp_password is None:
                raise ValueError("SMTP doit être authentifié dans cet environnement")
            if self.frontend_base_url.scheme != "https":
                raise ValueError("les liens doivent être en HTTPS dans cet environnement")
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
