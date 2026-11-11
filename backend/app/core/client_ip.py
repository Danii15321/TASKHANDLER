"""Adresse IP du client, sans faire confiance à un en-tête qu'il peut écrire lui-même."""

import ipaddress
from typing import Annotated

from fastapi import Depends, Request

from app.core.dependencies import SettingsDependency

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Au-delà, l'en-tête est ignoré : une chaîne de proxys légitime ne produit jamais une telle taille.
_MAX_FORWARDED_FOR_LENGTH = 2048
UNKNOWN_CLIENT = "unknown"


# requires : `value` est une adresse candidate.
# assigns  : rien (fonction pure).
# ensures  : renvoie l'adresse IP analysée, ou `None` si `value` n'en est pas une.
def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


# requires : `trusted_proxies` liste les réseaux des proxys de l'infrastructure.
# assigns  : rien (fonction pure).
# ensures  : renvoie l'IP du client. Sans proxy de confiance devant l'API, c'est l'adresse de la
#            connexion, et `X-Forwarded-For` est ignoré. Derrière un proxy de confiance, l'en-tête
#            est lu de droite à gauche, et la première adresse qui n'est pas un proxy de confiance
#            est celle du client. Les entrées plus à gauche ont pu être écrites par le client.
def resolve_client_ip(
    peer: str | None, forwarded_for: list[str], trusted_proxies: tuple[IPNetwork, ...]
) -> str:
    if peer is None:
        return UNKNOWN_CLIENT
    peer_ip = _parse_ip(peer)
    if peer_ip is None or not any(peer_ip in network for network in trusted_proxies):
        return peer

    header = ",".join(forwarded_for)
    if not header or len(header) > _MAX_FORWARDED_FOR_LENGTH:
        return peer

    client = peer_ip
    for entry in reversed(header.split(",")):
        candidate = _parse_ip(entry)
        if candidate is None:
            # Entrée illisible : on s'arrête au dernier saut fiable plutôt que de deviner.
            break
        client = candidate
        if not any(candidate in network for network in trusted_proxies):
            break
    return str(client)


# requires : l'application a été assemblée par `create_app`.
# assigns  : rien.
# ensures  : renvoie l'IP du client de la requête, selon la règle de `resolve_client_ip`.
def get_client_ip(request: Request, settings: SettingsDependency) -> str:
    peer = request.client.host if request.client is not None else None
    return resolve_client_ip(
        peer, request.headers.getlist("x-forwarded-for"), settings.trusted_proxies
    )


ClientIpDependency = Annotated[str, Depends(get_client_ip)]
