#!/usr/bin/env bash
# Génère les fichiers d'environnement locaux, avec des secrets aléatoires propres à cette machine.
# Usage : scripts/generate-dev-env.sh [dossier_cible]
#
# Quatre fichiers, pour que chaque processus ne reçoive que ce dont il a besoin :
#   .env            l'API, avec le seul rôle applicatif
#   .env.migration  Alembic, avec le rôle de migration
#   .env.postgres   Docker Compose
#   .env.test       les tests, sur la base dédiée `taskhandler_test`
set -euo pipefail

# Lecture et écriture réservées au propriétaire pour tous les fichiers créés ici.
umask 077

target_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
port="${TASKHANDLER_POSTGRES_PORT:-55432}"
host="127.0.0.1:${port}"

# Ne jamais écraser un fichier : les secrets existants correspondent à un volume déjà initialisé.
for file in .env .env.migration .env.postgres .env.test; do
    if [[ -e "${target_dir}/${file}" ]]; then
        echo "Arrêt : ${file} existe déjà, aucun fichier n'a été modifié." >&2
        exit 1
    fi
done

# 256 bits d'aléa en hexadécimal : aucun caractère à échapper dans une URL.
postgres_password="$(openssl rand -hex 32)"
migrator_password="$(openssl rand -hex 32)"
app_password="$(openssl rand -hex 32)"
# Clé de signature des jetons : 256 bits, propre à cette machine.
jwt_secret="$(openssl rand -hex 32)"

app_url="postgresql+asyncpg://taskhandler_app:${app_password}@${host}"
migrator_url="postgresql+asyncpg://taskhandler_migrator:${migrator_password}@${host}"

cat >"${target_dir}/.env" <<EOF
TASKHANDLER_ENVIRONMENT=development
TASKHANDLER_LOG_LEVEL=INFO
TASKHANDLER_DATABASE_URL=${app_url}/taskhandler
TASKHANDLER_JWT_SECRET=${jwt_secret}
EOF

cat >"${target_dir}/.env.migration" <<EOF
TASKHANDLER_MIGRATION_ENVIRONMENT=development
TASKHANDLER_MIGRATION_DATABASE_URL=${migrator_url}/taskhandler
EOF

cat >"${target_dir}/.env.postgres" <<EOF
POSTGRES_PASSWORD=${postgres_password}
TASKHANDLER_MIGRATOR_PASSWORD=${migrator_password}
TASKHANDLER_APP_PASSWORD=${app_password}
TASKHANDLER_POSTGRES_PORT=${port}
EOF

cat >"${target_dir}/.env.test" <<EOF
TEST_APP_DATABASE_URL=${app_url}/taskhandler_test
TEST_MIGRATION_DATABASE_URL=${migrator_url}/taskhandler_test
EOF

# Seuls les noms sont affichés : un secret ne doit pas rester dans un historique de terminal.
echo "Fichiers créés dans ${target_dir} : .env .env.migration .env.postgres .env.test"
