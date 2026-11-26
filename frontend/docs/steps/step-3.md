# Phase 3 du frontend — Adaptation de l'API au navigateur

## Objectif

Jusqu'ici, l'API renvoyait le jeton de renouvellement dans le corps JSON. Dans un navigateur, ce
jeton finirait en mémoire JavaScript, lisible par le moindre script injecté : un vol donnerait
accès au compte pendant des jours. Cette phase le place dans un cookie que JavaScript ne peut
pas lire, sans ouvrir de faille CSRF.

Cette phase modifie **le backend** : toutes ses règles s'appliquent (étiquettes, tests de
sécurité, ruff, mypy, couverture de 95 %).

**Résultat attendu :** une page d'une autre origine ne peut ni renouveler ni fermer la session,
et aucune réponse de l'API ne contient plus le jeton de renouvellement.

## Décisions

| Question | Choix retenu | Raison |
|---|---|---|
| Flux par corps JSON | **Supprimé** : cookie uniquement | Aucun client hors navigateur ; une seule voie à sécuriser |
| Routes protégées du CSRF | `/auth/refresh` et `/auth/login` | Celle qui lit le cookie, et celle qui le pose |
| Déconnexion | Jeton d'accès + effacement du cookie | Le jeton d'accès n'est jamais envoyé seul : pas de CSRF |

## Étape 1 — Le cookie

Tout vit dans `backend/app/modules/auth/browser_session.py`.

| Attribut | Valeur | Ce qu'il empêche |
|---|---|---|
| Nom | `__Secure-refresh_token` | Le préfixe oblige le navigateur à refuser le cookie s'il n'est pas `Secure` |
| `HttpOnly` | oui | La lecture par JavaScript, donc le vol par une XSS |
| `Secure` | oui | L'envoi en clair |
| `SameSite` | `Strict` | L'envoi depuis une page d'un autre site |
| `Path` | `/api/v1/auth/refresh` | L'envoi vers toute autre route, et sa présence dans leurs journaux |
| `Domain` | absent | Le partage avec les sous-domaines : le cookie reste à l'hôte de l'API |
| `Max-Age` | expiration du jeton en base | Un cookie qui survivrait à son jeton |

> **Piège.** `SameSite=Strict` compare des **sites** (schéma et domaine enregistrable), pas des
> origines. `http://localhost:3000` et `http://localhost:8000` sont le même site ;
> `http://localhost:3000` et `http://127.0.0.1:8000` ne le sont pas, et le cookie ne partirait
> jamais. D'où `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000` en développement. En production,
> `app.taskhandler.…` et `api.taskhandler.…` partagent le même site.

`IssuedTokens` porte désormais `refresh_expires_at`, qui fixe `Max-Age`. `TokenResponse` ne
contient plus que `access_token`, `token_type` et `expires_in` ; `RefreshRequest` disparaît.

## Étape 2 — Le renouvellement lit le cookie

`POST /auth/refresh` ne lit plus de corps. Le cookie arrive par `Cookie(alias=…)`, déclaré
**facultatif** : un cookie absent doit donner le même `401` qu'un cookie invalide, et non une
erreur de validation `422` qui révélerait la différence.

1. `well_formed_refresh_token` vérifie le format exact de `secrets.token_urlsafe(32)` : une
   valeur fausse n'atteint jamais la base.
2. La rotation, la détection de réutilisation et la limite par utilisateur sont inchangées.
3. Tout échec renvoie `401 Session invalide ou expirée.` **et efface le cookie** : un cookie
   refusé une fois le sera toujours.

Une `HTTPException` ne porte que des en-têtes : `cleared_refresh_cookie_header()` fabrique
l'en-tête `Set-Cookie` d'effacement à partir d'une `Response` vide. Pour effacer, un navigateur
exige les mêmes nom, chemin et domaine que le dépôt : `clear_refresh_cookie` reprend les
attributs de `set_refresh_cookie`.

La connexion pose le cookie ; `logout` et `logout-all` l'effacent.

## Étape 3 — La protection CSRF

Un cookie part tout seul : une page piégée pourrait déclencher un renouvellement à la place de
l'utilisateur. Deux contrôles indépendants, en dépendances de route (`dependencies=[Depends(…)]`),
s'exécutent avant tout le reste :

1. **En-tête personnalisé** `X-CSRF-Protection: 1`. Un formulaire HTML ne sait pas l'envoyer ;
   `fetch` ne peut l'ajouter vers une autre origine qu'après une requête de contrôle CORS, que
   l'API refuse à toute origine hors liste.
2. **En-tête `Origin`**, comparé à `cors_allowed_origins`. Le navigateur le pose lui-même ;
   aucun script ne peut le falsifier.

| Route | En-tête | `Origin` absente | `Origin` hors liste |
|---|---|---|---|
| `/auth/refresh` | obligatoire | **refusée** : le cookie n'existe que dans un navigateur, qui l'envoie toujours | refusée |
| `/auth/login` | obligatoire | acceptée : outils d'administration ou de test | refusée |

Un refus répond `403 Requête refusée.`, sans dire quel contrôle a échoué, et écrit l'événement
d'audit `csrf_rejected` avec sa raison. L'origine reçue n'est **pas** journalisée : c'est une
valeur contrôlée par l'appelant. Le jeton n'est jamais consommé par une requête refusée.

> **Pourquoi les deux ?** Chacun couvre la défaillance de l'autre. Si CORS était mal configuré,
> l'origine serait encore vérifiée ; si un navigateur n'envoyait pas `Origin`, l'en-tête
> personnalisé imposerait encore la requête de contrôle. C'est la défense en profondeur.

## Étape 4 — CORS avec identifiants

Dans `app/main.py`, `allow_credentials=True` et `X-CSRF-Protection` rejoint `allow_headers`. Ce
droit ne vaut que pour les origines explicites de `TASKHANDLER_CORS_ALLOWED_ORIGINS` (jamais `*`,
refusé par la validation de la configuration). Une liste vide ferme CORS et rend le
renouvellement impossible : c'est sûr par défaut.

En développement, `.env.example` et `scripts/generate-dev-env.sh` déclarent
`["http://localhost:3000"]` et `TASKHANDLER_FRONTEND_BASE_URL=http://localhost:3000` (port de
Next.js, et non plus 5173).

> **À savoir.** Starlette ajoute `Access-Control-Allow-Credentials: true` même au refus (`400`)
> d'une origine étrangère. Sans `Access-Control-Allow-Origin` correspondant, cet en-tête seul est
> sans effet : le navigateur bloque la requête. Le test vérifie donc le refus, pas son absence.

## Étape 5 — Tests

Les tests écrits **avant** le code (`tests/test_browser_session.py`, 21 cas) décrivent le contrat :

- connexion : cookie posé avec chaque attribut, aucun `refresh_token` dans le corps ;
- le chemin du cookie est exactement celui de la route (`url_path_for("refresh")`) ;
- renouvellement : nouveau cookie ; l'ancien flux par corps donne `401` sans consommer le jeton ;
- cookie absent, vide, trop court, inconnu, hors alphabet ou de 4 000 caractères : même `401`,
  qui efface le cookie ;
- sans en-tête, avec `Origin: null`, une origine étrangère ou sans origine : `403`, journalisé,
  jeton intact, ni origine ni jeton dans les journaux ;
- connexion depuis une autre origine : `403`, aucun cookie, aucune session ouverte ;
- déconnexion simple ou globale : cookie effacé, jeton refusé ensuite.

Le contrat OpenAPI gagne deux gardes : login et refresh portent leur dépendance anti-CSRF, et
`refresh_token` n'apparaît plus dans aucun schéma publié.

Les tests existants passent par des aides communes de `tests/conftest.py` : `post_login`,
`post_refresh` (cookie et en-têtes envoyés comme le ferait le navigateur) et `tokens_of`.

> **Piège.** Le client de test parle HTTP : `http.cookiejar` ne renverrait jamais seul un cookie
> `Secure`. Chaque test envoie donc le cookie explicitement, ce qui rend aussi visible ce qui
> part réellement.

## Vérification

Depuis `backend/` :

```bash
uv run --locked ruff check . && uv run --locked ruff format --check . && uv run --locked mypy .
uv run --locked alembic check
uv run --locked coverage run -m pytest && uv run --locked coverage report
```

Aucune migration : le schéma de la base ne change pas.

## Ce que cette phase ne fait pas

Le frontend n'appelle pas encore l'API : le client HTTP, le jeton en mémoire et la coordination
entre onglets arrivent en phase 4. Le guide `docs/steps/step-5.md` du backend décrit l'ancien
flux par corps : il reste comme trace historique de la phase 5.
