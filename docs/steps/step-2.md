# Phase 2 — PostgreSQL et migrations

## Objectif

Disposer d'une base versionnée, accessible avec le **moindre privilège** et testée en conditions
réelles.

**Résultat attendu :** une migration s'applique puis s'annule proprement, et un test prouve que le
rôle applicatif ne peut pas exécuter `DROP TABLE`.

Le code complet et commenté se trouve dans `backend/`. Ce guide en explique les décisions.

## Étape 1 — Installer les dépendances

Depuis `backend/` :

```bash
uv add 'sqlalchemy[asyncio]' asyncpg alembic
```

L'extra `asyncio` installe `greenlet`, indispensable à SQLAlchemy asynchrone. Pour les tests
asynchrones, le plugin pytest d'`anyio` suffit : il est déjà présent via Starlette, et une
dépendance en moins est une surface d'attaque en moins.

> **Bonne pratique.** Avant d'ajouter un paquet, vérifiez son nom exact (typosquatting), sa date de
> dernière publication et ses vulnérabilités connues.

## Étape 2 — Deux rôles, deux secrets

| Rôle | Droits | Utilisé par | Fichier |
|---|---|---|---|
| `taskhandler_migrator` | Possède le schéma (DDL) | Alembic | `.env.migration` |
| `taskhandler_app` | `SELECT`, `INSERT`, `UPDATE`, `DELETE` | L'API | `.env` |

Si une injection SQL ou un défaut de l'API survient, l'attaquant reste limité aux droits du rôle
applicatif : pas de `DROP`, pas de `TRUNCATE`, pas de changement de schéma. Le superutilisateur
`postgres` ne sert qu'à l'initialisation du conteneur.

`scripts/generate-dev-env.sh` crée quatre fichiers non versionnés, en mode `600`, avec des secrets
de 256 bits tirés par `openssl rand`. Il refuse d'écraser un fichier existant et n'affiche aucun
secret. Chaque processus ne reçoit ainsi que ce dont il a besoin.

## Étape 3 — PostgreSQL avec Compose (`compose.yaml`)

Quatre détails comptent pour la sécurité :

- `127.0.0.1:55432:5432` : sans l'adresse, Docker publie le port sur tout le réseau local.
- `image: postgres:18-bookworm@sha256:…` : un tag peut être republié, pas une empreinte.
- `${POSTGRES_PASSWORD:?…}` : Compose s'arrête si le secret manque, sans valeur de repli.
- Le script d'initialisation est monté en lecture seule (`:ro`), avec `no-new-privileges`.

`docker/postgres/init-roles.sh` crée les rôles et les bases `taskhandler` et `taskhandler_test`.
Les mots de passe sont lus avec `\getenv`, puis insérés avec `:'variable'`, que psql échappe :

```sql
\getenv app_password TASKHANDLER_APP_PASSWORD
CREATE ROLE taskhandler_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
REVOKE ALL ON DATABASE taskhandler FROM PUBLIC;
GRANT CONNECT ON DATABASE taskhandler TO taskhandler_app;
```

Sans ce `REVOKE`, n'importe quel rôle du serveur peut se connecter et créer des tables temporaires.

Ce script ne s'exécute qu'au **premier** démarrage d'un volume vide.

```bash
scripts/generate-dev-env.sh
docker compose --env-file .env.compose up --detach --wait
```

## Étape 4 — Valider l'URL de connexion (`app/core/config.py`)

`database_url` est un `SecretStr` **sans valeur par défaut** : sans lui, l'application ne démarre
pas. `_validate_database_url` refuse, sans jamais recopier l'URL dans l'erreur :

| Refus | Risque évité |
|---|---|
| Pilote autre que `postgresql+asyncpg` | Un pilote synchrone bloquerait la boucle d'événements |
| Rôle différent de celui attendu | L'API démarrerait en superutilisateur ou en rôle de migration |
| Mot de passe court ou d'exemple | Un `.env.example` copié tel quel partirait en production |
| Pas de `ssl=verify-full` en production | Un intermédiaire pourrait se faire passer pour le serveur |

`ssl=require` chiffre sans vérifier le certificat : il ne protège pas d'une interception active.

`MigrationSettings` est une classe distincte, qui lit `.env.migration`. Grâce à `extra="forbid"`,
placer le secret de migration dans le `.env` de l'API empêche le démarrage.

## Étape 5 — Le moteur et la session (`app/db/session.py`)

```python
create_async_engine(
    url, hide_parameters=True, pool_size=..., max_overflow=..., pool_timeout=...,
    pool_pre_ping=True,
    connect_args={"timeout": ..., "command_timeout": ...,
                  "server_settings": {"statement_timeout": "5000", "lock_timeout": "2000",
                                      "idle_in_transaction_session_timeout": "10000",
                                      "timezone": "UTC", "application_name": "taskhandler-api"}},
)
```

- `hide_parameters=True` : les valeurs liées, donc des données personnelles, restent hors des
  messages d'erreur et des journaux.
- `statement_timeout` passe par les paramètres de connexion : aucune requête n'y échappe, et
  c'est PostgreSQL qui l'applique. `command_timeout` couvre le cas d'un réseau figé.
- Tous ces réglages sont bornés dans `Settings` : pool et délais ne sont jamais illimités.

`get_session` fournit une session par requête. Une exception annule la transaction, et une session
fermée sans `commit` n'écrit rien : le commit est toujours **explicite**, dans la couche service.

`expire_on_commit=False` évite qu'une lecture d'attribut après un commit ne déclenche une requête
implicite, interdite dans ce contexte asynchrone.

`create_app` crée le moteur sans ouvrir de connexion : `/health/live` répond même si la base est
en panne. Le `lifespan` ferme le pool à l'arrêt.

> **Sécurité : injection SQL.** N'écrivez jamais de f-string, de `%` ou de concaténation pour
> construire du SQL. Utilisez `text("... :valeur")` ou les requêtes SQLAlchemy, toujours liées.

## Étape 6 — Alembic (`alembic.ini`, `migrations/`)

- `alembic.ini` ne contient **aucune URL** : `migrations/env.py` la lit dans `MigrationSettings`.
- Le dossier s'appelle `migrations/` et non `alembic/`, qui masquerait le paquet du même nom.
- `lock_timeout` à 5 secondes : une migration bloquée ne doit pas figer toute l'application.
- `Base.metadata.create_all` reste interdit : le schéma n'évolue que par des migrations relues.

Dans `app/db/base.py`, définissez `naming_convention`. Sans convention, PostgreSQL choisit le nom
des contraintes, et une migration ne peut plus les retrouver de façon fiable.

## Étape 7 — La première migration

Elle ne crée aucune table : elle pose le **cadre des droits**, dont toutes les suivantes héritent.

```python
op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
op.execute("GRANT USAGE ON SCHEMA public TO taskhandler_app")
op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
           "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO taskhandler_app")
```

Les privilèges par défaut s'appliquent aux tables **créées ensuite** par le rôle de migration :
aucune migration future ne peut oublier ces droits. `TRUNCATE` est exclu, et `alembic_version`,
créée avant, reste inaccessible à l'API. Le `downgrade` retire tout et rétablit l'état initial.

```bash
uv run --locked alembic upgrade head
```

## Étape 8 — La sonde `GET /health/ready`

La route lance `SELECT 1` avec un délai de 2 secondes. Quelle que soit la panne, elle renvoie un
`503` générique, identique pour un réseau coupé ou un mot de passe refusé.

> **Sécurité.** Cette route est publique. Elle ne donne ni version, ni hôte, ni nom de base, et le
> journal ne garde que le **type** de l'erreur, car son message peut décrire la connexion.

La limitation du débit de cette route arrive à la phase 6.

## Étape 9 — Tester sur un vrai PostgreSQL

SQLite est interdit : il ignorerait les rôles, `statement_timeout` et les contraintes avancées.

- `DatabaseTestSettings` lit `.env.test` et **refuse toute base autre que `taskhandler_test`** : les
  tests annulent des migrations, et ils ne doivent jamais pouvoir toucher une autre base.
- `isolated_session` place chaque test dans une transaction annulée à la fin ; avec
  `join_transaction_mode="create_savepoint"`, même les `commit` du code testé sont annulés.
- Une URL absente fait **échouer** les tests : un test ignoré donnerait une fausse confiance.

Vérifiez au minimum :

- rôle applicatif : `DROP`, `ALTER`, `TRUNCATE`, `CREATE` refusés avec le code SQL `42501` ;
- `alembic_version` inaccessible à l'API, aucun attribut d'administration sur les deux rôles ;
- `upgrade`, puis `downgrade`, puis `upgrade`, en contrôlant les droits réellement posés ;
- annulation des écritures après une erreur, ou sans `commit` ;
- délais réellement appliqués, valeurs liées absentes des erreurs SQL ;
- `/health/ready` : succès, base injoignable, base figée, mot de passe refusé.

Depuis `backend/`, ces commandes doivent toutes réussir :

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy .
uv run --locked pytest
```

## À retenir

- Le moindre privilège se **prouve** par un test qui tente l'opération interdite.
- Une valeur par défaut pour un secret est un secret publié.
- La vérification TLS est contrôlée dans la configuration ; le déploiement réel arrive à la phase 11.

**Normes concernées :** OWASP API Security Top 10 2023, API4 (consommation) et API8 (configuration) ;
OWASP ASVS, accès aux données et gestion des secrets ; CIS PostgreSQL Benchmark, moindre privilège.
