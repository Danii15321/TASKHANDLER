"""Tests du TLS sortant : PostgreSQL, Redis et SMTP vérifient toujours le certificat du serveur."""

import ssl
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.rate_limit import redis_tls_options
from app.core.tls import client_tls_context
from app.db.session import database_connect_args
from tests.conftest import UNUSED_CREDENTIAL, unreachable_database_url

TEST_CA = Path(__file__).parent / "data" / "test-ca.crt"
TLS_REDIS_URL = SecretStr(f"rediss://taskhandler_app:{UNUSED_CREDENTIAL}@redis:6380/0")


# requires : `overrides` contient des réglages valides.
# assigns  : rien.
# ensures  : renvoie une configuration de production, avec TLS vers chaque service.
def production_settings(**overrides: object) -> Settings:
    return Settings.model_validate(
        {
            "database_url": unreachable_database_url("?ssl=verify-full"),
            "redis_url": TLS_REDIS_URL,
            **overrides,
        }
    )


# requires : rien.
# assigns  : rien.
# ensures  : le contexte exige un certificat valide pour le nom d'hôte visé, et TLS 1.2 au moins.
def test_client_context_verifies_certificate_and_hostname() -> None:
    context = client_tls_context(production_settings())

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


# requires : le certificat public de l'autorité de test existe.
# assigns  : rien.
# ensures  : l'autorité interne s'ajoute aux autorités du système, sans les remplacer : un
#            fournisseur SMTP public reste vérifiable à côté de PostgreSQL et Redis internes.
def test_internal_ca_is_added_to_system_authorities() -> None:
    without_ca = client_tls_context(production_settings())
    with_ca = client_tls_context(production_settings(tls_ca_file=TEST_CA))

    assert "TaskHandler test CA" in str(with_ca.get_ca_certs())
    assert len(with_ca.get_ca_certs()) == len(without_ca.get_ca_certs()) + 1


# requires : `tmp_path` est un dossier vide propre au test.
# assigns  : rien.
# ensures  : une autorité introuvable empêche le démarrage, au lieu d'échouer à la première
#            connexion.
def test_missing_ca_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        production_settings(tls_ca_file=tmp_path / "absente.crt")


# requires : rien.
# assigns  : rien.
# ensures  : avec `ssl=verify-full`, la connexion PostgreSQL utilise le contexte vérifié, qui
#            connaît l'autorité interne ; sans TLS (développement), aucun contexte n'est imposé.
def test_database_uses_verified_context_only_with_tls() -> None:
    production = database_connect_args(production_settings(tls_ca_file=TEST_CA))
    development = database_connect_args(
        Settings(environment="development", database_url=unreachable_database_url())
    )

    context = production["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert "ssl" not in development


# requires : rien.
# assigns  : rien.
# ensures  : avec `rediss://`, Redis vérifie le certificat et le nom d'hôte, avec l'autorité
#            interne ; avec `redis://` (développement), aucune option TLS n'est passée.
def test_redis_tls_options() -> None:
    production = redis_tls_options(production_settings(tls_ca_file=TEST_CA))
    development = redis_tls_options(
        Settings(
            environment="development",
            database_url=unreachable_database_url(),
            redis_url=SecretStr(f"redis://taskhandler_app:{UNUSED_CREDENTIAL}@127.0.0.1:1/0"),
        )
    )

    assert production == {
        "ssl_cert_reqs": "required",
        "ssl_check_hostname": True,
        "ssl_min_version": ssl.TLSVersion.TLSv1_2,
        "ssl_ca_certs": str(TEST_CA),
    }
    assert development == {}
