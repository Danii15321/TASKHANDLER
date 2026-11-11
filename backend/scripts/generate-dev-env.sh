#!/usr/bin/env bash
# Génère les fichiers d'environnement locaux, avec des secrets aléatoires propres à cette machine.
# Usage : scripts/generate-dev-env.sh [dossier_cible]
#
# Quatre fichiers, pour que chaque processus ne reçoive que ce dont il a besoin :
#   .env            l'API, avec le seul rôle applicatif
#   .env.migration  Alembic, avec le rôle de migration
#   .env.compose    Docker Compose (PostgreSQL, Redis et Mailpit)
#   .env.test       les tests, sur la base dédiée `taskhandler_test`
set -euo pipefail

# Lecture et écriture réservées au propriétaire pour tous les fichiers créés ici.
umask 077

target_dir="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
port="${TASKHANDLER_POSTGRES_PORT:-55432}"
host="127.0.0.1:${port}"
redis_port="${TASKHANDLER_REDIS_PORT:-56379}"
smtp_port="${TASKHANDLER_SMTP_PORT:-51025}"
mailpit_ui_port="${TASKHANDLER_MAILPIT_UI_PORT:-58025}"

# Ne jamais écraser un fichier : les secrets existants correspondent à un volume déjà initialisé.
for file in .env .env.migration .env.compose .env.test; do
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
redis_password="$(openssl rand -hex 32)"

app_url="postgresql+asyncpg://taskhandler_app:${app_password}@${host}"
migrator_url="postgresql+asyncpg://taskhandler_migrator:${migrator_password}@${host}"
redis_url="redis://taskhandler_app:${redis_password}@127.0.0.1:${redis_port}/0"

cat >"${target_dir}/.env" <<EOF
TASKHANDLER_ENVIRONMENT=development
TASKHANDLER_LOG_LEVEL=INFO
TASKHANDLER_DATABASE_URL=${app_url}/taskhandler
TASKHANDLER_JWT_SECRET=${jwt_secret}
TASKHANDLER_REDIS_URL=${redis_url}
# Mailpit garde les e-mails : interface sur http://127.0.0.1:${mailpit_ui_port}
TASKHANDLER_SMTP_HOST=127.0.0.1
TASKHANDLER_SMTP_PORT=${smtp_port}
TASKHANDLER_SMTP_SECURITY=none
TASKHANDLER_EMAIL_FROM=ne-pas-repondre@taskhandler.fr
TASKHANDLER_FRONTEND_BASE_URL=http://localhost:5173
EOF

cat >"${target_dir}/.env.migration" <<EOF
TASKHANDLER_MIGRATION_ENVIRONMENT=development
TASKHANDLER_MIGRATION_DATABASE_URL=${migrator_url}/taskhandler
EOF

cat >"${target_dir}/.env.compose" <<EOF
POSTGRES_PASSWORD=${postgres_password}
TASKHANDLER_MIGRATOR_PASSWORD=${migrator_password}
TASKHANDLER_APP_PASSWORD=${app_password}
TASKHANDLER_POSTGRES_PORT=${port}
TASKHANDLER_REDIS_PASSWORD=${redis_password}
TASKHANDLER_REDIS_PORT=${redis_port}
TASKHANDLER_SMTP_PORT=${smtp_port}
TASKHANDLER_MAILPIT_UI_PORT=${mailpit_ui_port}
EOF

cat >"${target_dir}/.env.test" <<EOF
TEST_APP_DATABASE_URL=${app_url}/taskhandler_test
TEST_MIGRATION_DATABASE_URL=${migrator_url}/taskhandler_test
TEST_REDIS_URL=${redis_url}
TEST_SMTP_PORT=${smtp_port}
TEST_MAILPIT_API_URL=http://127.0.0.1:${mailpit_ui_port}
EOF

# Seuls les noms sont affichés : un secret ne doit pas rester dans un historique de terminal.
echo "Fichiers créés dans ${target_dir} : .env .env.migration .env.compose .env.test"
