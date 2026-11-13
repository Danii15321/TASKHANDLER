#!/usr/bin/env bash
# Crée les rôles et la base de production au tout premier démarrage du volume PostgreSQL.
# Les mots de passe sont lus dans les fichiers secrets, jamais dans l'environnement.
set -euo pipefail

# requires : `$1` est le nom d'un fichier de `/run/secrets`.
# assigns  : rien.
# ensures  : affiche son contenu ; échoue si le fichier manque ou est vide.
read_secret() {
    local value
    value="$(cat "/run/secrets/$1")"
    [ -n "$value" ] || { echo "secret $1 vide" >&2; exit 1; }
    printf '%s' "$value"
}

# Les secrets passent par l'environnement de `psql` seul, puis sont insérés avec `:'variable'`,
# que psql échappe : ni ligne de commande visible par `ps`, ni SQL concaténé.
MIGRATOR_PASSWORD="$(read_secret migrator_password)" \
APP_PASSWORD="$(read_secret app_password)" \
BACKUP_PASSWORD="$(read_secret backup_password)" \
psql --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname=postgres <<'SQL'
\getenv migrator_password MIGRATOR_PASSWORD
\getenv app_password APP_PASSWORD
\getenv backup_password BACKUP_PASSWORD

-- Mêmes rôles qu'en développement : le rôle de migration possède le schéma, l'application ne
-- possède rien.
CREATE ROLE taskhandler_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'migrator_password';
CREATE ROLE taskhandler_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'app_password';
-- Rôle de sauvegarde, en lecture seule : `pg_read_all_data` lui donne SELECT partout, sans aucun
-- droit d'écriture. `BYPASSRLS` est indispensable : sans lui, la RLS cacherait les tâches à
-- `pg_dump`, et la sauvegarde serait incomplète.
CREATE ROLE taskhandler_backup LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS
    PASSWORD :'backup_password';
GRANT pg_read_all_data TO taskhandler_backup;

CREATE DATABASE taskhandler OWNER taskhandler_migrator;
REVOKE ALL ON DATABASE taskhandler FROM PUBLIC;
GRANT CONNECT ON DATABASE taskhandler TO taskhandler_app, taskhandler_backup;
SQL
