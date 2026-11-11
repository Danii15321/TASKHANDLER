"""Tests de la configuration validée au démarrage."""

import secrets
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import (
    APP_DATABASE_ROLE,
    MIGRATION_DATABASE_ROLE,
    MigrationSettings,
    Settings,
)
from tests.conftest import UNUSED_CREDENTIAL, unreachable_database_url

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
ENV_EXAMPLE_PLACEHOLDER = "remplacer-par-un-secret-aleatoire"
# La clé JWT est laissée vide dans le modèle : une valeur d'exemple serait prise pour un secret
# par gitleaks, et une clé vide est de toute façon refusée au démarrage.
ENV_EXAMPLE_JWT_LINE = "TASKHANDLER_JWT_SECRET=\n"
DATABASE_URL_VARIABLE = "TASKHANDLER_DATABASE_URL"
TLS_QUERY = "?ssl=verify-full"


# requires : `role` est un nom de rôle PostgreSQL, `query` est vide ou commence par `?`.
# assigns  : rien.
# ensures  : renvoie une URL syntaxiquement correcte pour ce rôle.
def url_for_role(role: str, query: str = "") -> SecretStr:
    return SecretStr(f"postgresql+asyncpg://{role}:{UNUSED_CREDENTIAL}@db.local/app{query}")


# requires : la variable d'URL est absente de l'environnement.
# assigns  : rien.
# ensures  : sans URL de base, l'application refuse de démarrer.
def test_refuses_to_start_without_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# requires : aucune autre variable `TASKHANDLER_*` n'est définie.
# assigns  : la variable d'URL le temps du test.
# ensures  : sans autre réglage, l'application adopte le mode le plus strict.
def test_defaults_to_production_without_documentation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        DATABASE_URL_VARIABLE, url_for_role(APP_DATABASE_ROLE, TLS_QUERY).get_secret_value()
    )

    settings = Settings(_env_file=None)

    assert settings.environment == "production"
    assert settings.api_docs_enabled is False


# requires : rien.
# assigns  : les variables `TASKHANDLER_*` le temps du test.
# ensures  : les variables préfixées sont bien prises en compte.
def test_reads_prefixed_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKHANDLER_ENVIRONMENT", "development")
    monkeypatch.setenv(DATABASE_URL_VARIABLE, url_for_role(APP_DATABASE_ROLE).get_secret_value())

    assert Settings(_env_file=None).api_docs_enabled is True


# requires : rien.
# assigns  : la variable d'environnement `TASKHANDLER_ENVIRONMENT` le temps du test.
# ensures  : une valeur invalide est refusée, et l'erreur ne recopie pas cette valeur.
def test_refuses_invalid_value_without_echoing_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKHANDLER_ENVIRONMENT", "valeur-peut-etre-secrete")

    with pytest.raises(ValidationError) as error:
        Settings(database_url=url_for_role(APP_DATABASE_ROLE), _env_file=None)

    assert "valeur-peut-etre-secrete" not in str(error.value)


# requires : `tmp_path` est un dossier temporaire accessible en écriture.
# assigns  : crée un fichier de configuration temporaire.
# ensures  : une clé inconnue dans le fichier `.env` empêche le démarrage.
def test_refuses_unknown_key_in_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TASKHANDLER_ENVIRONEMENT=development\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings(database_url=url_for_role(APP_DATABASE_ROLE), _env_file=env_file)


# requires : `tmp_path` est un dossier temporaire accessible en écriture.
# assigns  : crée un fichier de configuration temporaire.
# ensures  : le secret de migration ne peut pas être placé dans la configuration de l'API.
def test_refuses_migration_url_in_application_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    migration_url = url_for_role(MIGRATION_DATABASE_ROLE).get_secret_value()
    env_file.write_text(f"TASKHANDLER_MIGRATION_DATABASE_URL={migration_url}\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings(database_url=url_for_role(APP_DATABASE_ROLE), _env_file=env_file)


# requires : le fichier `backend/.env.example` existe, `tmp_path` est accessible en écriture.
# assigns  : crée une copie du modèle, retire de l'environnement les réglages qu'il fournit.
# ensures  : renvoie le contenu du modèle dont seuls les secrets choisis ont été remplacés.
def fill_env_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, database: bool, jwt_secret: bool
) -> Path:
    # Sans cela, les valeurs fournies par `clean_environment` masqueraient celles du fichier.
    monkeypatch.delenv("TASKHANDLER_JWT_SECRET")
    monkeypatch.delenv("TASKHANDLER_REDIS_URL")
    for name in (
        "TASKHANDLER_SMTP_HOST",
        "TASKHANDLER_SMTP_PORT",
        "TASKHANDLER_SMTP_USERNAME",
        "TASKHANDLER_SMTP_PASSWORD",
        "TASKHANDLER_EMAIL_FROM",
        "TASKHANDLER_FRONTEND_BASE_URL",
    ):
        monkeypatch.delenv(name)
    content = ENV_EXAMPLE.read_text(encoding="utf-8")
    if database:
        content = content.replace(ENV_EXAMPLE_PLACEHOLDER, UNUSED_CREDENTIAL)
    if jwt_secret:
        content = content.replace(
            ENV_EXAMPLE_JWT_LINE, f"TASKHANDLER_JWT_SECRET={secrets.token_hex(32)}\n"
        )
    filled_file = tmp_path / ".env"
    filled_file.write_text(content, encoding="utf-8")
    return filled_file


# requires : `database` et `jwt_secret` ne valent pas tous les deux `True`.
# assigns  : crée une copie temporaire du modèle.
# ensures  : chaque valeur factice du modèle, à elle seule, empêche réellement le démarrage.
@pytest.mark.parametrize(("database", "jwt_secret"), [(False, False), (True, False), (False, True)])
def test_env_example_placeholders_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, database: bool, jwt_secret: bool
) -> None:
    env_file = fill_env_example(tmp_path, monkeypatch, database=database, jwt_secret=jwt_secret)

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)


# requires : le fichier `backend/.env.example` existe, `tmp_path` est accessible en écriture.
# assigns  : crée une copie temporaire du modèle.
# ensures  : le modèle est complet : seuls ses secrets factices restent à remplacer.
def test_env_example_is_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = fill_env_example(tmp_path, monkeypatch, database=True, jwt_secret=True)

    assert Settings(_env_file=env_file).environment == "development"


# requires : `bad_url` enfreint une règle de sécurité de la configuration.
# assigns  : rien.
# ensures  : l'URL est refusée, et aucun mot de passe n'apparaît dans le message d'erreur.
@pytest.mark.parametrize(
    "bad_url",
    [
        "ceci-n-est-pas-une-url",
        f"postgresql://taskhandler_app:{UNUSED_CREDENTIAL}@db.local/app",
        f"postgresql+asyncpg://postgres:{UNUSED_CREDENTIAL}@db.local/app",
        f"postgresql+asyncpg://taskhandler_migrator:{UNUSED_CREDENTIAL}@db.local/app",
        f"postgresql+asyncpg://taskhandler_app:{UNUSED_CREDENTIAL}@/app",
        f"postgresql+asyncpg://taskhandler_app:{UNUSED_CREDENTIAL}@db.local",
        "postgresql+asyncpg://taskhandler_app@db.local/app",
        "postgresql+asyncpg://taskhandler_app:trop-court@db.local/app",
        "postgresql+asyncpg://taskhandler_app:un-Password-exemple-assez-long@db.local/app",
    ],
)
def test_refuses_unsafe_database_url_without_echoing_it(bad_url: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(environment="test", database_url=SecretStr(bad_url), _env_file=None)

    for secret in (UNUSED_CREDENTIAL, "trop-court", "un-Password-exemple"):
        assert secret not in str(error.value)


# requires : `query` n'impose pas la vérification complète du certificat.
# assigns  : rien.
# ensures  : la production refuse toute connexion sans vérification complète du certificat.
@pytest.mark.parametrize("query", ["", "?ssl=require", "?ssl=verify-ca", "?ssl=disable"])
def test_production_requires_verified_tls(query: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=url_for_role(APP_DATABASE_ROLE, query), _env_file=None)


# requires : rien.
# assigns  : rien.
# ensures  : la production accepte une connexion dont le certificat est entièrement vérifié.
def test_production_accepts_verified_tls() -> None:
    Settings(database_url=url_for_role(APP_DATABASE_ROLE, TLS_QUERY), _env_file=None)


# requires : rien.
# assigns  : rien.
# ensures  : le mot de passe n'apparaît pas dans la représentation de la configuration.
def test_database_url_is_hidden_in_repr() -> None:
    settings = Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)

    assert UNUSED_CREDENTIAL not in repr(settings)


# requires : `field` est un réglage borné, `value` est hors de ses limites.
# assigns  : rien.
# ensures  : un délai ou un pool hors limites empêche le démarrage.
@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TASKHANDLER_DATABASE_POOL_SIZE", "0"),
        ("TASKHANDLER_DATABASE_POOL_SIZE", "1000"),
        ("TASKHANDLER_DATABASE_STATEMENT_TIMEOUT_MS", "0"),
        ("TASKHANDLER_DATABASE_STATEMENT_TIMEOUT_MS", "3600000"),
    ],
)
def test_refuses_out_of_bounds_database_limits(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)


# requires : rien.
# assigns  : rien.
# ensures  : la configuration d'Alembic n'accepte que le rôle de migration.
def test_migration_settings_require_migrator_role() -> None:
    with pytest.raises(ValidationError):
        MigrationSettings(
            environment="test", database_url=url_for_role(APP_DATABASE_ROLE), _env_file=None
        )

    settings = MigrationSettings(
        environment="test", database_url=url_for_role(MIGRATION_DATABASE_ROLE), _env_file=None
    )
    assert settings.environment == "test"


# requires : rien.
# assigns  : rien.
# ensures  : Alembic exige lui aussi un certificat vérifié hors des environnements locaux.
def test_migration_settings_require_verified_tls_in_production() -> None:
    with pytest.raises(ValidationError):
        MigrationSettings(database_url=url_for_role(MIGRATION_DATABASE_ROLE), _env_file=None)
