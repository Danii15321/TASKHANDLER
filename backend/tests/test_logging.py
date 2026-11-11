"""Tests du formateur de journaux et de son filtrage des données sensibles."""

import json
import logging
import sys
from time import perf_counter

import pytest

from app.core.logging_config import (
    MAX_LOG_EXCEPTION_LENGTH,
    MAX_LOG_VALUE_LENGTH,
    REDACTED,
    JsonFormatter,
    _scrub_text,
)
from app.core.request_context import bind_request_id, reset_request_id

FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl"
FAKE_ARGON2_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"

# Marge très large : un filtrage linéaire prend quelques millisecondes sur ces entrées, alors
# qu'un filtrage quadratique prend plusieurs secondes.
MAX_SCRUB_SECONDS = 1.0


# requires : les champs de `extra` ne portent pas le nom d'un attribut standard de journal.
# assigns  : rien.
# ensures  : renvoie le texte brut produit par le formateur pour cet événement.
def format_record(message: str, extra: dict[str, object] | None = None) -> str:
    record = logging.makeLogRecord(
        {"name": "test", "levelname": "INFO", "msg": message, **(extra or {})}
    )
    return JsonFormatter().format(record)


# requires : `key` désigne une donnée sensible.
# assigns  : rien.
# ensures  : la valeur du champ n'apparaît jamais dans le journal.
@pytest.mark.parametrize(
    "key",
    ["password", "Authorization", "password_hash", "refresh_token", "task_description", "cookie"],
)
def test_redacts_sensitive_fields(key: str) -> None:
    output = format_record("event", {key: "valeur-sensible"})

    assert "valeur-sensible" not in output
    assert json.loads(output)[key] == REDACTED


# requires : rien.
# assigns  : rien.
# ensures  : le filtrage s'applique aussi aux champs imbriqués.
def test_redacts_nested_sensitive_fields() -> None:
    output = format_record("event", {"context": {"user": {"password": "valeur-sensible"}}})

    assert "valeur-sensible" not in output


# requires : `secret_text` contient un secret dans un texte libre.
# assigns  : rien.
# ensures  : le secret est retiré du message journalisé.
@pytest.mark.parametrize(
    ("secret_text", "secret"),
    [
        ("en-tête : Bearer abc.def-ghi", "abc.def-ghi"),
        (f"jeton reçu {FAKE_JWT}", FAKE_JWT),
        (f"empreinte {FAKE_ARGON2_HASH}", FAKE_ARGON2_HASH),
        ("connexion password=hunter2 refusée", "hunter2"),
        ("refresh_token=abc123 reçu", "abc123"),
        ("{'password': 'mot de passe'}", "mot de passe"),
        ("échec vers postgresql+asyncpg://role:valeur-base@db:5432/app", "valeur-base"),
    ],
)
def test_scrubs_secrets_from_free_text(secret_text: str, secret: str) -> None:
    assert secret not in format_record(secret_text)


# requires : rien.
# assigns  : rien.
# ensures  : les secrets contenus dans une trace d'exception sont retirés.
def test_scrubs_secrets_from_exception() -> None:
    try:
        raise ValueError(f"jeton invalide : {FAKE_JWT}")
    except ValueError:
        record = logging.makeLogRecord(
            {"name": "test", "levelname": "ERROR", "msg": "event", "exc_info": sys.exc_info()}
        )

    output = JsonFormatter().format(record)

    assert FAKE_JWT not in output
    assert "ValueError" in output


# requires : rien.
# assigns  : rien.
# ensures  : un retour à la ligne reçu d'un client ne peut pas créer une fausse ligne de journal.
def test_neutralizes_log_injection() -> None:
    output = format_record("event", {"path": '/x\n{"level": "CRITICAL", "message": "faux"}'})

    assert len(output.splitlines()) == 1


# requires : rien.
# assigns  : rien.
# ensures  : une valeur trop longue est tronquée.
def test_truncates_long_values() -> None:
    output = format_record("event", {"path": "a" * (MAX_LOG_VALUE_LENGTH * 2)})

    assert len(json.loads(output)["path"]) == MAX_LOG_VALUE_LENGTH


# requires : rien.
# assigns  : rien.
# ensures  : une trace d'exception géante est tronquée en gardant sa fin, la plus utile.
def test_truncates_long_exception_keeping_its_end() -> None:
    try:
        raise ValueError("a" * (MAX_LOG_EXCEPTION_LENGTH * 2) + "fin-du-message")
    except ValueError:
        record = logging.makeLogRecord(
            {"name": "test", "levelname": "ERROR", "msg": "event", "exc_info": sys.exc_info()}
        )

    exception_text = json.loads(JsonFormatter().format(record))["exception"]

    assert len(exception_text) == MAX_LOG_EXCEPTION_LENGTH
    assert exception_text.endswith("fin-du-message")


# requires : `pattern` est un motif répété pour piéger les expressions régulières.
# assigns  : rien.
# ensures  : le filtrage reste rapide : un client ne peut pas bloquer le serveur (ReDoS).
@pytest.mark.parametrize(
    "pattern",
    ["token", "password-", "token ", "token='", "eyJ", "bearer ", "pg://a:", "a.", "a://b:c"],
)
def test_scrubbing_resists_hostile_input(pattern: str) -> None:
    hostile_text = pattern * 8000

    started_at = perf_counter()
    _scrub_text(hostile_text)

    assert perf_counter() - started_at < MAX_SCRUB_SECONDS


# requires : rien.
# assigns  : l'identifiant de requête du contexte le temps du test.
# ensures  : chaque événement porte l'identifiant de la requête en cours.
def test_includes_request_id() -> None:
    token = bind_request_id("identifiant-de-test")
    try:
        output = format_record("event")
    finally:
        reset_request_id(token)

    assert json.loads(output)["request_id"] == "identifiant-de-test"


# requires : rien.
# assigns  : rien.
# ensures  : un champ `extra` ne peut pas usurper un champ réservé du journal.
def test_extras_cannot_override_reserved_fields() -> None:
    output = format_record("event", {"level_override": "x", "request_id": "usurpé"})

    assert json.loads(output)["request_id"] is None
