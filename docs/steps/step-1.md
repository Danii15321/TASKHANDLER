# Phase 1 — Application FastAPI minimale

## Objectif

Construire une application sans logique métier, mais déjà **sûre par défaut** et **observable**.

**Résultat attendu :** une erreur interne renvoie un message générique et un identifiant, qui
permet de retrouver la trace complète dans les journaux.

Le code complet et commenté se trouve dans `backend/app/`. Ce guide en explique les décisions.

## Étape 1 — Installer les dépendances

Depuis `backend/` :

```bash
uv add fastapi uvicorn pydantic-settings
uv add --dev httpx2
```

`httpx2` sert uniquement aux tests : c'est le client HTTP attendu par `TestClient`. Avec l'ancien
`httpx`, Starlette émet un avertissement d'obsolescence, que `filterwarnings = ["error"]` a
justement transformé en échec. On change de paquet, on ne fait pas taire l'avertissement.

Dans `pyproject.toml`, ajoutez `pythonpath = ["."]` à pytest et `plugins = ["pydantic.mypy"]` à
mypy, puis créez `tests/__init__.py`.

## Étape 2 — La configuration (`app/core/config.py`)

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TASKHANDLER_", env_file=ENV_FILE, env_file_encoding="utf-8",
        extra="forbid", frozen=True, hide_input_in_errors=True,
    )
    environment: Environment = "production"  # Literal["development", "test", "production"]
    log_level: LogLevel = "INFO"
```

Chaque option répond à un risque précis :

| Choix | Risque évité |
|---|---|
| `production` par défaut | Une variable oubliée ne doit jamais affaiblir la sécurité |
| `Literal` | `prodcution` est refusé au démarrage au lieu d'activer un comportement imprévu |
| `extra="forbid"` | Une faute de frappe dans `.env` ne désactive pas un réglage sans bruit |
| `frozen=True` | La configuration ne change pas pendant l'exécution |
| `hide_input_in_errors=True` | Le message d'erreur ne recopie pas une valeur, peut-être secrète |

Créez aussi `.env.example`, avec des valeurs factices uniquement.

## Étape 3 — Le contexte de requête (`app/core/request_context.py`)

Un identifiant unique par requête relie la réponse du client aux journaux. Il est stocké dans une
`ContextVar`, isolée par tâche asynchrone : une variable globale mélangerait les requêtes
simultanées. Écrivez `get_request_id`, `bind_request_id` et `reset_request_id`.

## Étape 4 — Les journaux filtrés (`app/core/logging_config.py`)

Ne nommez pas ce fichier `logging.py` : il masquerait le module standard, et ruff (A005) le refuse.

Créez un `JsonFormatter` qui écrit une ligne JSON par événement, et appliquez **deux barrières** :

1. **Par nom de champ** : tout champ dont le nom contient `password`, `token`, `authorization`,
   `cookie`, `secret`, `hash`, `body` ou `description` est remplacé par `[REDACTED]`.
2. **Dans le texte libre** : des expressions régulières retirent les `Bearer`, les JWT, les
   empreintes argon2 et les formes `password=...` des messages et des traces d'exception.

`configure_logging` utilise `dictConfig` et fait passer les journaux d'Uvicorn par ce formateur.
Le journal d'accès d'Uvicorn est désactivé, car il écrit la chaîne de requête.

> **Sécurité : injection de journaux.** `json.dumps` échappe les retours à la ligne : un client
> ne peut pas fabriquer une fausse ligne en plaçant `\n` dans une URL.
>
> **Sécurité : déni de service par expression régulière (ReDoS).** Ces filtres lisent des données
> contrôlables par un client. Une première version utilisait `token[\w-]*` : sur un texte répétant
> `token`, chaque occurrence reparcourait toute la fin du texte, soit plus d'une minute de calcul
> pour 40 Ko. La correction borne la répétition avec `[\w-]{0,40}`, et les valeurs sont tronquées
> avant filtrage. Testez toujours une expression régulière avec une entrée hostile.

## Étape 5 — Les erreurs RFC 9457 (`app/core/errors.py`)

Toutes les erreurs ont la même forme, avec le type `application/problem+json` :

```json
{"type": "about:blank", "title": "Not Found", "status": 404, "detail": "...", "request_id": "..."}
```

Écrivez `build_problem`, puis deux gestionnaires :

- `handle_http_exception` conserve le code et les en-têtes (`Allow`, plus tard `Retry-After`). Pour
  un code ≥ 500, le détail reste dans les journaux et le client reçoit un message générique.
- `handle_validation_error` renvoie seulement l'emplacement, le message et le type de chaque erreur.

> **Sécurité.** La réponse 422 par défaut de FastAPI contient le champ `input`, donc la valeur
> envoyée : un mot de passe invalide serait renvoyé, et possiblement mis en cache. On le retire.

Astuce mypy : enregistrez les gestionnaires avec `app.exception_handler(Exc)(handler)`, car
`add_exception_handler` refuse une signature qui attend un type d'exception précis.

## Étape 6 — Le middleware (`app/core/middleware.py`)

Écrivez un middleware **ASGI pur**, qui pour chaque requête :

1. génère l'identifiant avec `uuid4` et le place dans le contexte ;
2. ajoute `X-Request-ID` à la réponse ;
3. capture toute exception, la journalise avec sa trace et renvoie un 500 générique ;
4. journalise la méthode, le **chemin sans chaîne de requête**, le statut et la durée.

Pourquoi capturer ici ? Le gestionnaire de 500 de Starlette s'exécute à l'extérieur des
middlewares : sa réponse n'aurait plus accès au contexte. Si la réponse a déjà commencé, on ne peut
plus la remplacer : on relance l'exception pour que le serveur coupe la connexion.

> **Sécurité.** N'acceptez jamais l'identifiant envoyé par le client : il pourrait polluer les
> journaux ou brouiller une investigation.

## Étape 7 — La santé et l'assemblage

Créez `app/modules/health/` avec un `schemas.py` et un `router.py`. La route `GET /health/live`
renvoie `{"status": "ok"}` et rien d'autre : une sonde publique ne décrit pas l'infrastructure.

Dans `app/main.py`, écrivez la fabrique `create_app(settings=None)` :

```python
docs_enabled = resolved_settings.api_docs_enabled
app = FastAPI(
    title="TaskHandler API",
    debug=False,
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)
```

`debug` reste toujours à `False` : ce mode afficherait les traces au client. La documentation est
fermée en production, car le schéma complet de l'API serait une carte offerte à un attaquant.

Pour lancer l'application, depuis `backend/` :

```bash
TASKHANDLER_ENVIRONMENT=development uv run --locked uvicorn app.main:create_app --factory --no-server-header
```

`--no-server-header` évite d'annoncer le serveur utilisé dans chaque réponse.

## Étape 8 — Tester

Les tests sont dans `backend/tests/`. La fixture `clean_environment` isole chaque test de la
configuration de la machine. Vérifiez au minimum :

- production par défaut, valeur invalide refusée sans être recopiée, clé inconnue refusée ;
- champs sensibles masqués, y compris imbriqués, secrets retirés des textes et des traces ;
- absence d'injection de journaux, et résistance aux entrées hostiles ;
- `/docs`, `/redoc` et `/openapi.json` en 404 en production ;
- erreurs au format RFC 9457, sans recopie des valeurs reçues ;
- erreur imprévue : message générique, et même identifiant dans la réponse et dans le journal ;
- identifiant envoyé par le client ignoré, chaîne de requête absente des journaux.

Depuis `backend/`, ces commandes doivent toutes réussir :

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy .
uv run --locked pytest
```

## À retenir

- Sûr par défaut : c'est l'oubli qui doit être le cas le plus strict.
- Un filtre de journaux réduit le risque sans l'éliminer : la première règle reste de ne pas
  journaliser de donnée sensible.
- Les en-têtes de sécurité, CORS et la limite de taille des requêtes viennent aux phases 6 et 10.

**Normes concernées :** OWASP API Security Top 10 2023, API8 (mauvaise configuration) ; OWASP ASVS,
gestion des erreurs et journalisation ; RFC 9457.
