#!/usr/bin/env bash
# Crée les rôles et les bases au tout premier démarrage du volume PostgreSQL.
# `set -euo pipefail` : la moindre erreur interrompt l'initialisation au lieu de la laisser à moitié.
set -euo pipefail

# Les secrets sont lus avec `\getenv`, puis insérés avec `:'variable'`, que psql échappe : ils
# n'apparaissent ni dans la ligne de commande visible par `ps`, ni dans du SQL concaténé.
psql --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname=postgres <<'SQL'
\getenv migrator_password TASKHANDLER_MIGRATOR_PASSWORD
\getenv app_password TASKHANDLER_APP_PASSWORD

-- Le rôle de migration possède le schéma ; aucun des deux rôles n'est superutilisateur.
CREATE ROLE taskhandler_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'migrator_password';
-- Le rôle applicatif ne possède rien : ses droits lui sont accordés par la première migration.
CREATE ROLE taskhandler_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    PASSWORD :'app_password';

CREATE DATABASE taskhandler OWNER taskhandler_migrator;
CREATE DATABASE taskhandler_test OWNER taskhandler_migrator;

-- PUBLIC peut par défaut se connecter à toute base et y créer des tables temporaires.
REVOKE ALL ON DATABASE taskhandler FROM PUBLIC;
REVOKE ALL ON DATABASE taskhandler_test FROM PUBLIC;
GRANT CONNECT ON DATABASE taskhandler TO taskhandler_app;
GRANT CONNECT ON DATABASE taskhandler_test TO taskhandler_app;
SQL
