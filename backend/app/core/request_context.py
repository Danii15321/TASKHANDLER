"""Contexte de la requête en cours, accessible sans le passer de fonction en fonction."""

from contextvars import ContextVar, Token

# Une `ContextVar` est isolée par tâche asynchrone : deux requêtes simultanées ne peuvent pas
# se voir l'identifiant l'une de l'autre, contrairement à une variable globale.
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


# requires : rien.
# assigns  : rien (lecture seule).
# ensures  : renvoie l'identifiant de la requête en cours, ou `None` hors d'une requête.
def get_request_id() -> str | None:
    return _request_id.get()


# requires : `request_id` a été généré par le serveur, jamais fourni par le client.
# assigns  : l'identifiant de requête du contexte courant.
# ensures  : renvoie le jeton à transmettre à `reset_request_id` en fin de requête.
def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id.set(request_id)


# requires : `token` provient de l'appel à `bind_request_id` du même contexte.
# assigns  : l'identifiant de requête du contexte courant.
# ensures  : le contexte retrouve la valeur qu'il avait avant `bind_request_id`.
def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)
