#!/usr/bin/env bash
# Prépare la pile de production locale : autorité de certification, certificats des services,
# secrets aléatoires et paire de clés de sauvegarde. À lancer une seule fois, depuis la racine du
# dépôt : deploy/scripts/init-local.sh
set -euo pipefail

# Tout ce qui est créé ici n'est lisible que par son propriétaire, sauf mention contraire.
umask 077

root="$(cd "$(dirname "$0")/.." && pwd)"
local_dir="$root/local"
certs="$local_dir/certs"
secrets="$local_dir/secrets"
offline="$local_dir/offline"
api_host="api.taskhandler.localhost"

# Jamais d'écrasement : régénérer la clé de sauvegarde rendrait toutes les sauvegardes illisibles,
# et régénérer les secrets couperait l'accès à une base déjà initialisée.
if [ -e "$local_dir" ]; then
    echo "$local_dir existe déjà : rien n'est modifié." >&2
    exit 1
fi
mkdir -p "$certs" "$secrets" "$offline"
# Le dossier parent reste privé : les fichiers lisibles par les conteneurs (voir plus bas) ne le
# sont pas par les autres utilisateurs de la machine.
chmod 0700 "$local_dir" "$certs" "$secrets" "$offline"

# --- Autorité de certification interne -------------------------------------------------------
# ECDSA P-256 : clés courtes, signatures rapides, largement prises en charge. La clé de l'autorité
# va dans `offline/` : sur un vrai serveur, elle ne doit jamais y rester.
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
    -keyout "$offline/ca.key" -out "$certs/ca.crt" -subj "/CN=TaskHandler local CA" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null

# requires : `$1` est le nom du fichier, `$2` le nom d'hôte du service.
# assigns  : crée `$1.crt` et `$1.key` dans `$certs`, signés par l'autorité interne.
# ensures  : certificat serveur de 397 jours, valable pour ce seul nom d'hôte.
issue_certificate() {
    openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
        -keyout "$certs/$1.key" -out "$certs/$1.csr" -subj "/CN=$2" 2>/dev/null
    # La vérification du nom d'hôte porte sur `subjectAltName`, pas sur le CN.
    openssl x509 -req -in "$certs/$1.csr" -CA "$certs/ca.crt" -CAkey "$offline/ca.key" \
        -CAcreateserial -days 397 -out "$certs/$1.crt" -extfile <(printf '%s\n' \
            "subjectAltName=DNS:$2" "basicConstraints=critical,CA:FALSE" \
            "keyUsage=critical,digitalSignature" "extendedKeyUsage=serverAuth") 2>/dev/null
    rm -f "$certs/$1.csr"
    # Lisible dans les conteneurs, qui ne tournent pas sous l'UID de l'hôte ; le dossier `local/`
    # en 0700 l'interdit aux autres comptes de la machine. PostgreSQL en recopie une version 0600.
    chmod 0644 "$certs/$1.key"
}
issue_certificate postgres postgres
issue_certificate redis redis
issue_certificate mailpit mailpit
issue_certificate nginx "$api_host"
rm -f "$certs/ca.srl"
chmod 0644 "$certs"/*.crt

# --- Secrets -------------------------------------------------------------------------------------
# requires : `$1` est le nom du fichier, `$2` sa valeur.
# assigns  : écrit le secret dans `$secrets`, sans saut de ligne final.
# ensures  : fichier en lecture seule, lisible dans les conteneurs (voir `issue_certificate`).
write_secret() {
    printf '%s' "$2" > "$secrets/$1"
    chmod 0444 "$secrets/$1"
}
# requires : rien.
# assigns  : rien.
# ensures  : affiche 256 bits d'aléa en hexadécimal : aucun caractère à échapper dans une URL.
random_secret() { openssl rand -hex 32; }

postgres_password="$(random_secret)"
migrator_password="$(random_secret)"
app_password="$(random_secret)"
backup_password="$(random_secret)"
redis_password="$(random_secret)"
smtp_password="$(random_secret)"

write_secret postgres_password "$postgres_password"
write_secret migrator_password "$migrator_password"
write_secret app_password "$app_password"
write_secret backup_password "$backup_password"
write_secret redis_password "$redis_password"
write_secret jwt_secret "$(random_secret)"
write_secret smtp_password "$smtp_password"
write_secret mailpit_auth "taskhandler:$smtp_password"
write_secret app_database_url \
    "postgresql+asyncpg://taskhandler_app:$app_password@postgres:5432/taskhandler?ssl=verify-full"
write_secret migration_database_url \
    "postgresql+asyncpg://taskhandler_migrator:$migrator_password@postgres:5432/taskhandler?ssl=verify-full"
write_secret app_redis_url "rediss://taskhandler_app:$redis_password@redis:6380/0"

# --- Paire de clés de sauvegarde ---------------------------------------------------------------
# La clé privée sert uniquement à restaurer : elle va dans `offline/`. Sur un vrai serveur, elle
# est conservée hors de la machine (gestionnaire de mots de passe, support chiffré hors ligne).
# `docker run` direct, sans réseau ni volume : `compose run` monterait des fichiers pas encore
# créés, que Docker remplacerait par des dossiers appartenant à root.
docker compose -f "$root/compose.prod.yaml" build --quiet backup
docker run --rm --network none --entrypoint age-keygen taskhandler-backup:local \
    > "$offline/backup-key.txt" 2>/dev/null
chmod 0400 "$offline/backup-key.txt"
mkdir -p "$local_dir/backup"
chmod 0700 "$local_dir/backup"
grep -o 'age1[0-9a-z]*' "$offline/backup-key.txt" > "$local_dir/backup/recipient.txt"
chmod 0444 "$local_dir/backup/recipient.txt"

echo "Pile locale préparée dans $local_dir"
echo "À conserver hors du serveur en production : $offline (clé de l'autorité, clé de sauvegarde)."
