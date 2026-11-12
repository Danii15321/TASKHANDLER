#!/usr/bin/env bash
# Sauvegarde chiffrée de la base `taskhandler`, puis suppression des sauvegardes trop anciennes.
# `pipefail` : un échec de `pg_dump` fait échouer la sauvegarde, même au milieu du tube.
set -euo pipefail

# Fichiers créés lisibles par leur seul propriétaire.
umask 077

retention_days="${BACKUP_RETENTION_DAYS:-14}"
case "$retention_days" in
    '' | *[!0-9]*) echo "BACKUP_RETENTION_DAYS doit être un entier" >&2; exit 1 ;;
esac

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="/backups/taskhandler-${stamp}.dump.age"
partial="${target}.partial"
started="$(date +%s)"
# En cas d'échec, le fichier partiel est supprimé aussitôt : il ne reste que des sauvegardes
# complètes.
trap 'rm -f "$partial"' EXIT

# Le mot de passe vient du fichier secret ; il ne vit que dans l'environnement de ce script, jamais
# dans la ligne de commande visible par `ps`.
PGPASSWORD="$(cat /run/secrets/backup_password)"
export PGPASSWORD
# Certificat et nom d'hôte du serveur vérifiés, comme pour l'application.
export PGSSLMODE=verify-full PGSSLROOTCERT=/run/certs/ca.crt

# - format personnalisé (`-Fc`) : compressé, restaurable table par table ;
# - chiffré à la volée vers la clé publique : le dump en clair ne touche jamais le disque, et ce
#   serveur ne peut pas relire ses propres sauvegardes (la clé privée est hors ligne).
pg_dump --host=postgres --port=5432 --username=taskhandler_backup --dbname=taskhandler \
    --format=custom --no-password \
    | age --encrypt --recipients-file /run/backup/recipient.txt --output "$partial"

# Renommage atomique : une sauvegarde interrompue ne peut pas passer pour complète.
mv "$partial" "$target"

# Conservation bornée (RGPD) : un compte supprimé disparaît des sauvegardes après ce délai.
find /backups -maxdepth 1 -name 'taskhandler-*.dump.age' -mtime "+${retention_days}" -delete
find /backups -maxdepth 1 -name '*.partial' -mmin +60 -delete

# Une ligne JSON, sans secret : nom, taille et durée.
printf '{"event":"backup_completed","file":"%s","bytes":%s,"seconds":%s}\n' \
    "$(basename "$target")" "$(stat -c %s "$target")" "$(( $(date +%s) - started ))"
