"""Tests de la résolution de l'IP du client : `X-Forwarded-For` n'est jamais cru sur parole."""

import ipaddress

import pytest

from app.core.client_ip import UNKNOWN_CLIENT, IPNetwork, resolve_client_ip

PROXY = (ipaddress.ip_network("10.0.0.0/8"),)
NO_PROXY: tuple[IPNetwork, ...] = ()


# requires : `forwarded_for` est un en-tête écrit par un client, sans proxy de confiance.
# assigns  : rien.
# ensures  : sans proxy déclaré, l'en-tête est ignoré : un client ne peut pas changer d'IP en
#            l'écrivant lui-même, et donc pas contourner la limitation de débit.
@pytest.mark.parametrize("forwarded_for", [["203.0.113.9"], ["1.2.3.4, 5.6.7.8"], ["nimporte"]])
def test_header_is_ignored_without_trusted_proxy(forwarded_for: list[str]) -> None:
    assert resolve_client_ip("198.51.100.4", forwarded_for, NO_PROXY) == "198.51.100.4"


# requires : rien.
# assigns  : rien.
# ensures  : un client direct, hors des proxys de confiance, ne peut pas non plus usurper d'IP.
def test_header_is_ignored_from_untrusted_peer() -> None:
    assert resolve_client_ip("198.51.100.4", ["203.0.113.9"], PROXY) == "198.51.100.4"


# requires : rien.
# assigns  : rien.
# ensures  : derrière un proxy de confiance, l'adresse ajoutée par ce proxy est retenue.
def test_trusted_proxy_provides_client_ip() -> None:
    assert resolve_client_ip("10.0.0.2", ["203.0.113.9"], PROXY) == "203.0.113.9"


# requires : rien.
# assigns  : rien.
# ensures  : les entrées écrites à gauche par le client sont ignorées : seule compte la première
#            adresse, en partant de la droite, qui n'est pas un proxy de confiance.
def test_client_written_entries_are_ignored() -> None:
    header = ["1.1.1.1, 203.0.113.9, 10.0.0.7"]

    assert resolve_client_ip("10.0.0.2", header, PROXY) == "203.0.113.9"


# requires : rien.
# assigns  : rien.
# ensures  : plusieurs en-têtes `X-Forwarded-For` sont lus comme une seule liste.
def test_multiple_headers_are_combined() -> None:
    assert resolve_client_ip("10.0.0.2", ["1.1.1.1", "203.0.113.9"], PROXY) == "203.0.113.9"


# requires : `header` est un en-tête illisible ou démesuré.
# assigns  : rien.
# ensures  : une entrée illisible arrête la lecture au dernier saut fiable, sans rien deviner.
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (["pas-une-ip"], "10.0.0.2"),
        (["203.0.113.9, pas-une-ip"], "10.0.0.2"),
        (["1.1.1.1, " * 400 + "203.0.113.9"], "10.0.0.2"),
        ([], "10.0.0.2"),
    ],
)
def test_malformed_header_falls_back_to_peer(header: list[str], expected: str) -> None:
    assert resolve_client_ip("10.0.0.2", header, PROXY) == expected


# requires : rien.
# assigns  : rien.
# ensures  : les adresses IPv6 sont prises en charge.
def test_ipv6_is_supported() -> None:
    proxies = (ipaddress.ip_network("fd00::/8"),)

    assert resolve_client_ip("fd00::1", ["2001:db8::5"], proxies) == "2001:db8::5"


# requires : rien.
# assigns  : rien.
# ensures  : sans adresse de connexion, l'IP est marquée inconnue plutôt que d'en inventer une.
def test_missing_peer_is_unknown() -> None:
    assert resolve_client_ip(None, ["203.0.113.9"], PROXY) == UNKNOWN_CLIENT
