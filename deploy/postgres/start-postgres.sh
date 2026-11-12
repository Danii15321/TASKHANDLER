#!/usr/bin/env bash
# Démarre PostgreSQL avec TLS obligatoire. Lancé en root par Docker, il prépare la clé, puis
# rend la main à l'entrypoint officiel, qui abandonne aussitôt root pour l'utilisateur `postgres`.
set -euo pipefail

# PostgreSQL refuse une clé privée lisible par d'autres que lui : la clé montée (lisible par tous
# dans le conteneur) est recopiée sur un tmpfs, à `postgres`, en 0600.
chown postgres:postgres /run/postgres-tls
install -o postgres -g postgres -m 0600 /run/tls/postgres.key /run/postgres-tls/server.key
install -o postgres -g postgres -m 0644 /run/tls/postgres.crt /run/postgres-tls/server.crt

# - `ssl_min_protocol_version` : ni TLS 1.0 ni 1.1 ;
# - `hba_file` : seules les connexions TLS authentifiées par SCRAM sont admises ;
# - `password_encryption` : empreintes SCRAM-SHA-256, jamais MD5.
exec docker-entrypoint.sh postgres \
    -c ssl=on \
    -c ssl_cert_file=/run/postgres-tls/server.crt \
    -c ssl_key_file=/run/postgres-tls/server.key \
    -c ssl_min_protocol_version=TLSv1.2 \
    -c hba_file=/etc/postgresql/pg_hba.conf \
    -c password_encryption=scram-sha-256
