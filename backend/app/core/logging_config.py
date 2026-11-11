"""Journaux structurés en JSON, filtrés pour ne jamais contenir de donnée sensible.

Le fichier ne s'appelle pas `logging.py` pour ne pas masquer le module standard du même nom.
"""

import json
import logging
import logging.config
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from app.core.config import LogLevel
from app.core.request_context import get_request_id

REDACTED = "[REDACTED]"

# Longueur maximale d'une valeur journalisée : un client ne doit pas pouvoir remplir les
# disques avec des valeurs géantes, comme une URL de plusieurs dizaines de kilo-octets.
MAX_LOG_VALUE_LENGTH = 2048
MAX_LOG_EXCEPTION_LENGTH = 16_384

# Un champ dont le nom contient l'un de ces mots est masqué, quelle que soit sa valeur.
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "body",
    "cookie",
    "description",
    "hash",
    "passwd",
    "password",
    "pwd",
    "secret",
    "token",
)

# Attributs standards d'un `LogRecord` : tout autre attribut vient du paramètre `extra`.
_STANDARD_RECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {
    "asctime",
    "message",
}

# Deuxième barrière pour les secrets glissés dans un texte libre, comme un message d'exception.
# Ces expressions sont exécutées sur des données contrôlables par un client, et doivent donc
# rester linéaires pour éviter un déni de service par expression régulière (ReDoS) :
# - le motif JWT ne démarre qu'en début de mot ;
# - le suffixe du nom de champ est borné à 40 caractères. Avec `[\w-]*`, un texte répétant
#   `token` faisait parcourir toute la fin du texte à chaque occurrence : plus d'une minute
#   de calcul pour 40 Ko. `test_scrubbing_resists_hostile_input` protège contre ce retour.
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(r"(?<![\w-])eyJ[\w-]+\.[\w-]+\.[\w-]+")
_ARGON2_PATTERN = re.compile(r"\$argon2[a-z]*\$[^\s\"']+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)((?:password|passwd|pwd|secret|token)[\w-]{0,40}['\"]?\s*[=:]\s*)"
    r"(?:'[^']*'|\"[^\"]*\"|[^\s,;&}]+)"
)
# Une URL de connexion porte son mot de passe entre `:` et `@`. Toutes les répétitions sont
# bornées, pour la même raison que ci-dessus.
_URL_CREDENTIALS_PATTERN = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]{0,30}://[^\s:/@]{0,128}:)[^\s/@]{1,512}@"
)


# requires : rien.
# assigns  : rien (fonction pure).
# ensures  : renvoie le texte où chaque secret reconnu est remplacé par `REDACTED`.
def _scrub_text(text: str) -> str:
    scrubbed = _BEARER_PATTERN.sub(REDACTED, text)
    scrubbed = _JWT_PATTERN.sub(REDACTED, scrubbed)
    scrubbed = _ARGON2_PATTERN.sub(REDACTED, scrubbed)
    scrubbed = _URL_CREDENTIALS_PATTERN.sub(rf"\1{REDACTED}@", scrubbed)
    # Le nom du champ est conservé (`\1`) : savoir qu'il y en avait un aide au diagnostic.
    return _ASSIGNMENT_PATTERN.sub(rf"\1{REDACTED}", scrubbed)


# requires : rien.
# assigns  : rien (fonction pure).
# ensures  : renvoie `True` si le nom du champ désigne une donnée à ne jamais journaliser.
def _is_sensitive_key(key: str) -> bool:
    normalized_key = key.lower()
    return any(part in normalized_key for part in _SENSITIVE_KEY_PARTS)


# requires : `value` ne contient pas de référence circulaire.
# assigns  : rien (fonction pure).
# ensures  : renvoie une copie sérialisable en JSON, tronquée et sans donnée sensible reconnue.
def _sanitize(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    # Tronquer avant de filtrer : le coût du filtrage reste borné, quelle que soit l'entrée.
    return _scrub_text(str(value)[:MAX_LOG_VALUE_LENGTH])


class JsonFormatter(logging.Formatter):
    """Écrit chaque événement sur une ligne JSON, après filtrage des données sensibles."""

    # requires : `record` est un événement de journal valide.
    # assigns  : `record.exc_text`, comme le fait le formateur standard de Python.
    # ensures  : renvoie une ligne JSON unique, sans donnée sensible reconnue.
    def format(self, record: logging.LogRecord) -> str:
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_ATTRIBUTES
        }
        sanitized_extras = _sanitize(extras)
        payload: dict[str, object] = (
            dict(sanitized_extras) if isinstance(sanitized_extras, dict) else {}
        )
        # Ces champs sont écrits après les extras pour qu'un extra ne puisse pas les remplacer.
        payload.update(
            {
                "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": _sanitize(record.getMessage()),
                "request_id": get_request_id(),
            }
        )
        if record.exc_info:
            # On garde la fin de la trace : ses dernières lignes sont les plus utiles.
            exception_text = self.formatException(record.exc_info)[-MAX_LOG_EXCEPTION_LENGTH:]
            payload["exception"] = _scrub_text(exception_text)
        # `json.dumps` échappe les retours à la ligne et les caractères de contrôle : un client
        # ne peut donc pas fabriquer une fausse ligne de journal (injection de journaux).
        return json.dumps(payload, default=str)


# requires : `level` est un niveau de journalisation valide.
# assigns  : la configuration globale du module `logging` de Python.
# ensures  : tous les journaux, y compris ceux d'Uvicorn, sortent en JSON filtré sur stdout.
def configure_logging(level: LogLevel) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                },
            },
            "root": {"level": level, "handlers": ["stdout"]},
            "loggers": {
                # Sans ces entrées, Uvicorn écrirait dans son propre format, sans filtrage.
                "uvicorn": {"level": level, "handlers": [], "propagate": True},
                "uvicorn.error": {"level": level, "handlers": [], "propagate": True},
                # Le journal d'accès d'Uvicorn inclut la chaîne de requête : il est remplacé
                # par celui de `RequestContextMiddleware`, qui ne garde que le chemin.
                "uvicorn.access": {"handlers": [], "propagate": False},
            },
        }
    )
