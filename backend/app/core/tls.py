"""Contexte TLS des connexions sortantes : PostgreSQL, Redis et SMTP."""

import ssl

from app.core.config import Settings


# requires : `settings` est valide ; `tls_ca_file`, s'il est fourni, existe (vérifié au démarrage).
# assigns  : rien (lit seulement les certificats d'autorité).
# ensures  : renvoie un contexte qui exige un certificat valide pour le nom d'hôte visé, avec
#            TLS 1.2 au moins. Il fait confiance aux autorités du système et, en plus, à
#            l'autorité interne qui signe les certificats de PostgreSQL et de Redis.
def client_tls_context(settings: Settings) -> ssl.SSLContext:
    # `create_default_context` active déjà `CERT_REQUIRED` et `check_hostname` : un certificat
    # valide mais émis pour un autre serveur est refusé, ce qui bloque l'interception.
    context = ssl.create_default_context()
    # TLS 1.0 et 1.1 sont obsolètes (RFC 8996).
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if settings.tls_ca_file is not None:
        # Ajout, pas remplacement : un fournisseur SMTP public reste vérifiable par les
        # autorités du système.
        context.load_verify_locations(cafile=settings.tls_ca_file)
    return context
