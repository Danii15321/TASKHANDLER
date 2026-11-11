"""Curseur opaque de la liste des tâches (pagination par clé, et non par décalage).

Le curseur désigne la dernière tâche de la page : (`created_at`, `id`). La page suivante reprend
juste après elle, ce qui reste exact même si des tâches sont ajoutées entre deux pages, et coûte le
même prix à la page 1 et à la page 1 000, contrairement à `OFFSET`.

Le curseur n'est pas signé : un client qui le modifie ne fait que se déplacer dans ses propres
tâches, puisque la requête filtre toujours par propriétaire.
"""

import base64
import binascii
import uuid
from datetime import datetime

_SEPARATOR = "|"


# requires : `created_at` porte un fuseau horaire.
# assigns  : rien (fonction pure).
# ensures  : renvoie un curseur opaque, en base64 URL-safe, sans remplissage.
def encode_cursor(created_at: datetime, task_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}{_SEPARATOR}{task_id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


# requires : `cursor` est une valeur reçue d'un client.
# assigns  : rien (fonction pure).
# ensures  : renvoie (`created_at`, `id`) ; lève `ValueError` si le curseur n'a pas exactement la
#            forme produite par `encode_cursor`.
def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp, separator, identifier = raw.partition(_SEPARATOR)
        created_at = datetime.fromisoformat(timestamp)
        task_id = uuid.UUID(identifier)
    except binascii.Error, UnicodeError, ValueError:
        raise ValueError("curseur invalide") from None
    if not separator or created_at.tzinfo is None or encode_cursor(created_at, task_id) != cursor:
        raise ValueError("curseur invalide")
    return created_at, task_id
