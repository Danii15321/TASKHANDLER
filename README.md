# TaskHandler

TaskHandler est une application web qui permet de **planifier ses tâches** dans une grille hebdomadaire.

Ce dépôt couvre pour l'instant uniquement le **backend** : une API REST construite avec FastAPI et
PostgreSQL. Le frontend sera traité plus tard.

> **État du projet :** comptes, sessions, anti-abus, tâches et grille en place ; durcissement à venir. L'avancement réel se lit dans la
> [roadmap](docs/roadmap.md). Tout ce qui suit décrit la **cible** du projet.

---

## Fonctionnalités visées

### Comptes

- Inscription avec vérification de l'adresse e-mail
- Connexion, renouvellement de session et déconnexion
- Réinitialisation et changement du mot de passe
- Export et suppression du compte (RGPD)

### Tâches

- Une tâche possède obligatoirement un **titre** et une **description**
- Une tâche peut être placée sur plusieurs jours, et un jour contient plusieurs tâches (relation N-N)
- Pour chaque jour, la tâche a une heure de début et une durée en heures entières, sans échéance
- Statut par jour (`todo`, `in_progress`, `done`, `cancelled`) et priorité (`low`, `medium`, `high`)

### Grille hebdomadaire (interface visée)

Le futur frontend affichera un grand tableau : **une ligne par jour, une colonne par heure**. Une
tâche d'une heure occupe une case, une tâche de deux heures en occupe deux, et ainsi de suite. La
grille n'affiche que le titre ; la description apparaît au clic. L'API sert donc ces deux besoins
séparément : une grille légère, puis le détail d'une tâche.

### Hors périmètre de la première version

Frontend, tâches récurrentes, partage entre utilisateurs, notifications, authentification tierce.

---

## Stack technique

| Domaine | Choix | Raison |
|---|---|---|
| Langage | Python 3.14.10 (épinglé dans `backend/.python-version`) | Typage moderne, écosystème FastAPI |
| Framework API | FastAPI + Pydantic v2 | Validation stricte, OpenAPI généré |
| Base de données | PostgreSQL 18 | Contraintes fortes, transactions fiables |
| Accès aux données | SQLAlchemy 2 (asynchrone) + asyncpg | Requêtes paramétrées, typage |
| Migrations | Alembic | Schéma versionné et relu |
| Hachage des mots de passe | argon2id (`argon2-cffi`) | Recommandation OWASP, résistant au GPU |
| Jetons | PyJWT (accès) + jeton opaque rotatif (renouvellement) | Accès court, révocation possible |
| Configuration | pydantic-settings | Configuration validée au démarrage |
| Anti-abus | Redis | Compteurs partagés entre processus |
| Tests | pytest + httpx2 sur un vrai PostgreSQL | Comportement identique à la production |
| Qualité | ruff, mypy (mode strict), pre-commit, GitHub Actions | Erreurs détectées avant la relecture |
| Sécurité outillée | pip-audit, gitleaks, règles Bandit de ruff | Dépendances, secrets, code |
| Dépendances | uv + fichier de verrouillage | Installations reproductibles |

Chaque outil est introduit **par la phase qui en a besoin**, jamais à l'avance.

---

## Architecture cible

Le code est organisé par **domaine métier**, et chaque domaine est découpé en couches.

```text
.
├── README.md
├── CLAUDE.md
├── .github/workflows/       # Intégration continue
├── docs/
│   ├── roadmap.md           # Phases et tâches à cocher
│   └── steps/step-x.md      # Guide pédagogique détaillé de chaque phase
└── backend/
    ├── app/
    │   ├── main.py          # Assemblage de l'application
    │   ├── core/            # Configuration, sécurité, journaux, erreurs
    │   ├── db/              # Moteur, sessions, base déclarative
    │   └── modules/
    │       ├── health/
    │       ├── auth/
    │       ├── users/
    │       └── tasks/
    ├── migrations/          # Migrations Alembic versionnées
    └── tests/
```

Dans chaque module, les responsabilités sont séparées :

| Fichier | Responsabilité | Ne fait jamais |
|---|---|---|
| `router.py` | HTTP : routes, codes de statut, dépendances | De logique métier ni de SQL |
| `schemas.py` | Contrats d'entrée et de sortie (Pydantic) | D'accès aux données |
| `service.py` | Règles métier, transactions | De manipulation HTTP |
| `repository.py` | Requêtes SQL, toujours filtrées par propriétaire | De règle métier |
| `models.py` | Tables SQLAlchemy et contraintes | D'exposition directe à l'API |

---

## Modèle de données cible

| Table | Colonnes principales | Rôle |
|---|---|---|
| `users` | `id`, `email` (unique), `password_hash`, `email_verified_at`, `token_version`, `is_active` | Comptes |
| `refresh_tokens` | `id`, `user_id`, `family_id`, `token_hash`, `expires_at`, `session_expires_at`, `revoked_at`, `revocation_reason` | Sessions renouvelables |
| `one_time_tokens` | `id`, `user_id`, `purpose`, `token_hash`, `expires_at`, `used_at` | Vérification d'e-mail, réinitialisation |
| `tasks` | `id`, `user_id`, `title`, `description`, `priority` | Tâches |
| `task_days` | `task_id`, `user_id`, `day`, `start_hour`, `duration_hours`, `status`, `completed_at` — clé primaire (`task_id`, `day`) | Association N-N : placement d'une tâche sur un jour |

Conventions : identifiants UUID, horodatages `TIMESTAMPTZ` en UTC, jours en `DATE`, `created_at` et `updated_at` sur les
tables métier, suppression en cascade des données d'un utilisateur.

Dans `task_days`, `day` est une date réelle et les heures sont des entiers de 0 à 23, en heure locale.
PostgreSQL garantit qu'une tâche ne dépasse pas minuit et que deux tâches d'un même utilisateur
n'occupent jamais la même case.

---

## Aperçu de l'API cible

Toutes les routes métier sont préfixées par `/api/v1` et exigent un jeton d'accès, sauf mention
contraire.

| Domaine | Routes |
|---|---|
| Santé (publiques, hors préfixe) | `GET /health/live`, `GET /health/ready` |
| Authentification (publiques) | `POST /auth/register`, `/auth/verify-email`, `/auth/login`, `/auth/refresh`, `/auth/password/forgot`, `/auth/password/reset` |
| Session | `POST /auth/logout`, `POST /auth/logout-all` |
| Compte | `GET`, `DELETE /users/me`, `POST /users/me/password`, `GET /users/me/export` |
| Tâches | `GET`, `POST /tasks`, `GET`, `PATCH`, `DELETE /tasks/{id}` |
| Grille | `GET /planning?start=…&end=…` (sans description), `PUT`, `PATCH`, `DELETE /tasks/{id}/days/{day}` |

Les erreurs suivent le format `application/problem+json` (RFC 9457).

---

## Sécurité

La sécurité est une exigence de conception, pas une étape finale. Principes retenus :

- **Mots de passe** hachés avec argon2id, jamais journalisés ni renvoyés
- **Sessions** : jeton d'accès de 15 minutes, jeton de renouvellement opaque, haché en base, à
  rotation obligatoire et avec détection de réutilisation
- **Isolation stricte** : chaque requête est filtrée par propriétaire, et la ressource d'un autre
  utilisateur répond `404`
- **Validation** stricte des entrées et réponses filtrées par un schéma explicite
- **Anti-abus** : limitation de débit, tailles de requêtes et de pages bornées
- **Moindre privilège** : le compte PostgreSQL de l'application ne peut pas modifier le schéma
- **Aucun secret dans le dépôt**, vérification automatique à chaque changement

Le référentiel suivi est l'**OWASP API Security Top 10**, complété par l'**OWASP ASVS**. Les règles
détaillées sont dans [CLAUDE.md](CLAUDE.md).

---

## Méthode de travail

Le projet avance **phase par phase**, sans scaffold global.

1. La [roadmap](docs/roadmap.md) liste les phases et leurs tâches à cocher.
2. Chaque phase dispose d'un guide `docs/steps/step-x.md`, assez pédagogique pour qu'un humain
   puisse écrire le code lui-même.
3. Le code, les dépendances et l'environnement n'apparaissent que lorsqu'une phase les requiert.
4. Aucun commit sans autorisation explicite ; `main` n'évolue que par pull request depuis `daniel_dev`.

Toute fonction est abondamment commentée et précédée des étiquettes `requires`, `assigns` et
`ensures`, décrites en français :

```python
# requires : `email` est une adresse déjà normalisée.
# assigns  : rien (lecture seule).
# ensures  : renvoie l'utilisateur correspondant, ou `None` s'il n'existe pas.
async def get_user_by_email(session: AsyncSession, email: str) -> User | None: ...
```

Tailles maximales : 200 lignes pour `README.md`, `CLAUDE.md` et chaque `step-x.md`, 500 lignes pour
`docs/roadmap.md`.

---

## Installation

Prérequis : Git, Docker, OpenSSL et [uv](https://docs.astral.sh/uv/). Depuis `backend/` :

```bash
uv sync --locked
uv run --locked pre-commit install
scripts/generate-dev-env.sh
docker compose --env-file .env.compose up --detach --wait
uv run --locked alembic upgrade head
```

Pour lancer les tests, puis l'API en développement, toujours depuis `backend/` :

```bash
uv run --locked pytest
uv run --locked uvicorn app.main:create_app --factory --no-server-header --no-proxy-headers
```
