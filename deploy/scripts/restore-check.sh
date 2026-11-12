#!/usr/bin/env bash
# Test de restauration : déchiffre une sauvegarde, la restaure dans une pile neuve et isolée, puis
# vérifie que l'API la sert. Mesure la durée totale (objectif de temps de restauration).
# Depuis la racine du dépôt : deploy/scripts/restore-check.sh [fichier.dump.age]
# `KEEP=1` garde la pile restaurée en marche après le test, pour l'examiner.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
compose_file="$root/compose.prod.yaml"
source_project="taskhandler-prod"
project="${RESTORE_PROJECT:-taskhandler-restore}"
key="$root/local/offline/backup-key.txt"
pg_image="postgres:18-bookworm@sha256:f1ea2a1a4fb7b68d4836be1fdcb8a6466a89b072158bf9d97663600538c294c5"

# Garde-fou : ce script supprime les volumes du projet de restauration à la fin. Il ne doit
# jamais viser la pile de production.
if [ "$project" = "$source_project" ]; then
    echo "Refus : le projet de restauration ne peut pas être $source_project." >&2
    exit 1
fi
[ -r "$key" ] || { echo "Clé privée de sauvegarde introuvable : $key" >&2; exit 1; }

# Pile isolée : autres sous-réseaux, autre port, volumes propres au projet.
export TASKHANDLER_EDGE_SUBNET=172.30.110.0/24
export TASKHANDLER_DATA_SUBNET=172.30.120.0/24
export TASKHANDLER_HTTPS_PORT="${RESTORE_HTTPS_PORT:-8444}"
# requires : `$project` n'est pas la pile de production (vérifié plus haut).
# assigns  : ce que fait la commande Compose demandée, sur le seul projet de restauration.
# ensures  : exécute `docker compose` avec les arguments donnés.
compose() { docker compose -p "$project" -f "$compose_file" "$@"; }
# Le détail de Compose n'est affiché qu'en cas d'échec.
log="$(mktemp)"

# requires : rien.
# assigns  : supprime les conteneurs et les volumes du projet de restauration, sauf si KEEP=1.
# ensures  : aucune donnée restaurée ne reste sur la machine après le test.
cleanup() {
    local code=$?
    if [ "$code" -ne 0 ]; then
        echo "Échec de la restauration (code $code). Détail :" >&2
        tail -n 20 "$log" >&2
    fi
    if [ "${KEEP:-0}" != "1" ]; then
        compose --profile tools down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
    rm -f "$log"
}
trap cleanup EXIT

backup="${1:-}"
if [ -z "$backup" ]; then
    backup="$(docker run --rm --network none -v "${source_project}_backups:/backups:ro" \
        --entrypoint sh taskhandler-backup:local -c 'ls -1 /backups/*.dump.age | tail -n 1' \
        | xargs -r basename)"
fi
[ -n "$backup" ] || { echo "Aucune sauvegarde trouvée." >&2; exit 1; }
echo "Sauvegarde restaurée : $backup"
started="$(date +%s)"

# 1. Base vide, avec les mêmes rôles et le même TLS qu'en production.
compose up --detach --wait postgres redis mailpit >>"$log" 2>&1

# 2. Déchiffrement et restauration en flux : le dump en clair ne touche jamais le disque.
#    `--single-transaction` et `--exit-on-error` : tout ou rien.
#    Le déchiffrement lit deux fichiers de propriétaires différents (la clé, à l'utilisateur ; les
#    sauvegardes, à `postgres`) : il tourne en root, mais avec la seule capacité de lecture, sans
#    réseau et sur un système de fichiers en lecture seule.
docker run --rm --network none --read-only --user 0 --cap-drop ALL --cap-add DAC_READ_SEARCH \
    --security-opt no-new-privileges:true -v "${source_project}_backups:/backups:ro" \
    -v "$key:/run/backup-key.txt:ro" --entrypoint age taskhandler-backup:local \
    --decrypt --identity /run/backup-key.txt "/backups/$backup" \
| docker run --rm -i --network "${project}_data" \
    -v "$root/local/secrets/migrator_password:/run/secrets/migrator_password:ro" \
    -v "$root/local/certs/ca.crt:/run/certs/ca.crt:ro" \
    -e PGSSLMODE=verify-full -e PGSSLROOTCERT=/run/certs/ca.crt \
    --entrypoint bash "$pg_image" -c \
    'PGPASSWORD="$(cat /run/secrets/migrator_password)" pg_restore --host=postgres \
        --username=taskhandler_migrator --dbname=taskhandler --no-password \
        --single-transaction --exit-on-error'

# 3. Migration (doit être sans effet : la version est restaurée), API et proxy.
compose up --detach --wait >>"$log" 2>&1
compose logs migrate 2>/dev/null | grep -q "Running upgrade" \
    && { echo "Échec : la base restaurée n'était pas à jour." >&2; exit 1; }

# 4. Vérifications : l'API répond à travers TLS, et les données sont présentes.
status="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$root/local/certs/ca.crt" \
    "https://api.taskhandler.localhost:${TASKHANDLER_HTTPS_PORT}/health/ready")"
[ "$status" = "200" ] || { echo "Échec : /health/ready répond $status." >&2; exit 1; }
counts="$(compose exec -T -u postgres postgres psql -d taskhandler -At -c \
    "SELECT format('comptes=%s tâches=%s jours=%s sessions=%s',
        (SELECT count(*) FROM users), (SELECT count(*) FROM tasks),
        (SELECT count(*) FROM task_days), (SELECT count(*) FROM refresh_tokens))")"

echo "Restauration vérifiée en $(( $(date +%s) - started )) s : $counts"
