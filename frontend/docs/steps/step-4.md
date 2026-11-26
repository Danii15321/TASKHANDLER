# Phase 4 du frontend — Client API et gestion de session

## Objectif

Un unique point d'accès à l'API, typé et validé, et une session qui tient dans la durée sans
jamais stocker de jeton : le jeton d'accès vit en mémoire, le cookie `HttpOnly` (phase 3) permet
de restaurer la session au rechargement, et plusieurs onglets cohabitent sans se gêner.

**Résultat attendu :** deux onglets ouverts plus de 15 minutes gardent leur session, et aucun
jeton n'apparaît dans `localStorage`, `sessionStorage` ni `document.cookie`.

## Étape 1 — Relier le frontend au contrat de l'API

**Choix validé : zod comme source unique, vérifiée contre le contrat.** Le générateur habituel
(openapi-typescript) exige TypeScript 5 et tire une dizaine de dépendances vieillissantes. zod 4
n'a aucune dépendance, valide à l'exécution et sait produire un JSON Schema.

1. **Le backend exporte son contrat** dans `frontend/src/api/openapi.json`. Le test
   `test_frontend_contract_is_current` le compare au contrat réel et échoue s'il est périmé :
   ```bash
   UPDATE_API_CONTRACT=1 uv run --locked pytest -k contract   # depuis backend/
   ```
2. **`src/api/schemas.ts`** décrit chaque échange en zod. Les types en sont déduits
   (`z.infer`) : aucun type d'API n'est écrit deux fois.
3. **`src/api/contract.test.ts`** convertit chaque schéma (`z.toJSONSchema`) et le compare au
   composant du même nom : champs, champs obligatoires, types, constantes, refus des champs
   inconnus, à toute profondeur. Les bornes peuvent être plus strictes côté frontend. Un test
   témoin vérifie que le comparateur détecte bien un champ manquant.

Le contrat fait une boucle fermée : modifier l'API oblige à régénérer `openapi.json` (sinon la
CI backend échoue), ce qui fait échouer la CI frontend tant que les schémas zod ne suivent pas.

> **Sécurité.** `TokenResponseSchema` est un `strictObject` : si l'API renvoyait par erreur un
> `refresh_token` dans le corps, la réponse entière serait rejetée. `ProblemSchema` est un
> `looseObject`, comme le contrat : la RFC 9457 autorise des champs d'extension.

## Étape 2 — Le client HTTP unique

`src/api/http-client.ts` est le seul code qui appelle `fetch` vers l'API. Il est construit avec
ses dépendances (`createHttpClient({ apiOrigin, fetch })`), ce qui le rend testable sans réseau.

| Règle | Mise en œuvre |
|---|---|
| Destination limitée à l'API | Chemin obligatoirement sous `/api/v1/` ; `//`, `\`, `..`, `#` refusés ; origine revérifiée |
| Jeton uniquement dans l'en-tête | `Authorization: Bearer …`, jamais dans l'URL ni le corps |
| Cookie seulement où il le faut | `credentials: "include"` et `X-CSRF-Protection: 1` pour les routes de session, `"omit"` ailleurs |
| Pas de redirection | `redirect: "error"` : une redirection emmènerait la requête ailleurs |
| Pas de cache | `cache: "no-store"`, `referrerPolicy: "no-referrer"` |
| Délai maximal | 10 s par `AbortSignal.timeout`, combiné au signal de l'appelant |
| Réponse non fiable | Type de contenu, taille (1 Mo), JSON valide, puis schéma zod |

Les erreurs (`src/api/errors.ts`) deviennent une `ApiError` de **catégorie** (`unauthorized`,
`conflict`, `rate_limited`…), avec le code, `Retry-After` (secondes entières seulement) et
l'identifiant de requête. Le message affiché est celui de la catégorie, en français : **jamais**
le détail textuel de l'API ni le message d'une exception.

## Étape 3 — Le gestionnaire de session

`src/session/session-manager.ts` reçoit ses dépendances : client HTTP, verrous, canal entre
onglets, programmateur de rappels. Il expose `restore`, `login`, `logout`, `logoutAll`,
`request` et un état public (`unknown`, `authenticated`, `anonymous`, `unreachable`) qui ne
contient jamais le jeton.

- **Jeton en mémoire** : une variable du module, rien d'autre. Fermer l'onglet l'efface.
- **Renouvellement programmé** une minute avant l'expiration, d'après `expires_in`, sans
  décoder le JWT.
- **Une seule fois à la fois dans l'onglet** : la promesse en cours est partagée. Le mode
  strict de React, qui monte deux fois les effets en développement, ne double donc rien.
- **Rejeu unique** : une requête refusée en `401` déclenche un renouvellement, puis un seul
  nouvel essai. Si la session est perdue : état `anonymous`, aucune boucle.
- **Échec passager** (réseau, `503`, `429`) : la session n'est pas perdue ; nouvel essai après
  30 s, ou après le `Retry-After` de l'API s'il est plus long.
- **Déconnexion** : l'état local est effacé **avant** l'appel à l'API, et même si elle ne répond
  pas (l'erreur est alors relayée pour que l'interface la signale). Un compteur de génération
  empêche un renouvellement parti avant d'annuler la déconnexion à son retour.

## Étape 4 — Plusieurs onglets

L'API fait tourner le jeton de renouvellement et traite un second usage du même jeton comme un
vol : toute la session est coupée. Deux onglets qui se rechargent ensemble enverraient le même
cookie en même temps.

- **Web Locks** (`navigator.locks.request`) : tous les renouvellements de l'origine passent par
  le verrou `taskhandler:session-refresh`. Le second part après le premier, avec le cookie déjà
  renouvelé. Sans Web Locks, un verrou propre à l'onglet sert de repli : le pire cas est une
  session coupée, jamais une faille.
- **BroadcastChannel** `taskhandler:session` : connexion et déconnexion sont annoncées aux autres
  onglets. Les messages ne contiennent **aucun jeton** et sont validés par zod à la réception.

## Étape 5 — Branchement dans l'application

`src/session/browser-session.ts` assemble le gestionnaire unique de l'onglet. `use-session.ts`
l'expose à React par `useSyncExternalStore` : le rendu serveur voit toujours `unknown`, car le
serveur ne connaît jamais la session. `SessionBootstrap`, placé dans `layout.tsx`, lance la
restauration au chargement.

> **Piège.** Une méthode d'objet passée à React perd son `this` (`unbound-method`) : les
> fonctions de `sessionStore` sont donc des fonctions fléchées.

## Deux pièges de `next dev`

- **`AGENTS.md`** : dès qu'il détecte un agent IA, `next dev` écrit ce fichier à la racine du
  frontend, et le recrée s'il disparaît. `agentRules: false` dans `next.config.ts` le désactive :
  aucun fichier produit par l'outillage n'entre dans le dépôt.
- **Types en double** : `next dev` génère ses types de routes dans `.next/dev/types`, `next build`
  et `next typegen` dans `.next/types`. Les deux déclarent les mêmes types globaux : dès que les
  deux dossiers coexistent, `tsc` et le build échouent (`Duplicate identifier 'LayoutProps'`).
  Chaque script efface donc ceux de l'autre mode (`rm -rf .next/types` pour `dev`,
  `rm -rf .next/dev` pour `build` et `typecheck`). `next dev` exige `.next/dev/types/**/*.ts`
  dans `tsconfig.json` et réécrit le fichier s'il manque : on le laisse.

## Étape 6 — Tests

La fausse API de `session-manager.test.ts` reproduit le backend : rotation, réutilisation qui
coupe la session, jeton d'accès de 15 minutes, cookie partagé entre onglets et joint à l'envoi.

| Ce qui est prouvé | Comment |
|---|---|
| Deux onglets se chargent ensemble sans couper la session | `Promise.all` de deux restaurations, verrou partagé |
| Le verrou est indispensable | Témoin : verrous séparés → réutilisation détectée, session perdue |
| Plus de 15 minutes à deux onglets | Minuteurs simulés, 31 minutes : requêtes réussies, aucune réutilisation |
| Pas de boucle sur une session révoquée | Deux renouvellements au plus, état `anonymous` |
| Déconnexion propagée, déconnexion qui échoue | État effacé partout ; erreur relayée |

Le seuil de couverture de 95 % a signalé le câblage navigateur non testé : il l'est désormais
(Web Locks simulé, vrai `BroadcastChannel` de Node, minuteurs simulés).

## Vérification dans un vrai navigateur

API lancée avec `TASKHANDLER_CORS_ALLOWED_ORIGINS='["http://localhost:3000"]'`, frontend sous
`next dev` avec `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`, compte de test jetable vérifié
par Mailpit, puis supprimé.

- La connexion renvoie `access_token`, `token_type`, `expires_in` ; `document.cookie` est vide.
- Deux onglets rechargés **au même instant** : chacun fait un seul `POST /auth/refresh` → `200`,
  l'un après l'autre côté API, et aucun événement `refresh_token_reused`.
- `localStorage`, `sessionStorage` et `document.cookie` sont vides ; aucune base IndexedDB ;
  aucun JWT dans le DOM.

Le maintien au-delà de 15 minutes est prouvé par les tests à minuteurs simulés, et non par une
attente réelle.

## Ce que cette phase ne fait pas

Aucun écran : formulaires de connexion et d'inscription en phase 5.
