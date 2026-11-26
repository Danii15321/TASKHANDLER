# Phase 1 du frontend — Application minimale sûre par défaut

## Objectif

Avant le premier écran métier, l'application doit déjà se défendre : une configuration validée
au démarrage, une politique de sécurité du contenu (CSP) qui bloque tout script injecté, et des
pages d'erreur qui ne révèlent rien.

Pourquoi maintenant ? Le jeton d'accès vivra en mémoire JavaScript (phase 4). Un script injecté
pourrait le lire : la CSP est la défense principale, elle doit exister avant le jeton.

**Résultat attendu :** un script en ligne sans nonce est bloqué par le navigateur.

**Prérequis :** phase 0 terminée. Commandes depuis `frontend/`, après `nvm use`.

## Étape 1 — Valider la configuration publique

Toute variable `NEXT_PUBLIC_*` est **recopiée dans le JavaScript envoyé au navigateur**, au
build. Un secret nommé ainsi par erreur serait publié. `src/config/public-config.ts` impose donc :

1. **une liste fermée** (`ALLOWED_PUBLIC_VARIABLES`) : toute autre variable `NEXT_PUBLIC_*`
   arrête le build. Le message cite le nom, jamais la valeur ;
2. **une adresse d'API stricte** : `NEXT_PUBLIC_API_BASE_URL` doit être une URL HTTPS (HTTP n'est
   toléré que sous `next dev`), sans identifiants, chemin, requête ni fragment. On n'en garde que
   l'origine, qui servira aussi à la directive `connect-src`.

Ces contrôles sont appelés en tête de `next.config.ts`, évalué au build, au démarrage de
`next start` et de `next dev` : une configuration invalide n'atteint jamais un utilisateur.

```bash
cp .env.example .env.local   # jamais versionné ; valeur factice dans .env.example
```

> **Piège.** Next.js ne remplace par sa valeur que l'accès **littéral**
> `process.env.NEXT_PUBLIC_API_BASE_URL`. Passer `process.env` entier à une fonction lirait
> l'environnement du serveur au démarrage, qui peut différer du build : le navigateur et la CSP
> verraient alors deux adresses différentes. `loadPublicConfig()` fait cet accès littéral ;
> `src/env.d.ts` déclare la variable pour que TypeScript l'accepte malgré
> `noPropertyAccessFromIndexSignature`.

`next typegen` charge la configuration avec la même phase que le build : le script `typecheck` lui
fournit donc une adresse fictive explicite, `https://api.typegen.invalid` (le domaine `.invalid`
est réservé et ne résout jamais), qui ne sert qu'à générer des types.

## Étape 2 — Une CSP à nonce, générée à chaque requête

Un **nonce** est un secret aléatoire neuf à chaque page servie. Le serveur l'inscrit dans la CSP
et sur chaque balise `<script>` légitime ; un script injecté ne le connaît pas et ne s'exécute pas.

- `src/security/content-security-policy.ts` : `createNonce()` tire 16 octets avec
  `crypto.getRandomValues` (jamais `Math.random`), et `buildContentSecurityPolicy()` assemble la
  politique, fonction pure facile à tester.
- `src/proxy.ts` : depuis Next.js 16, l'ancien `middleware.ts` s'appelle **proxy**. Il génère le
  nonce, pose la CSP sur la **réponse** et sur la **requête** transmise au rendu : Next.js lit le
  nonce dans cet en-tête de requête et l'ajoute lui-même à ses scripts.

| Directive | Valeur en production | Rôle |
|---|---|---|
| `default-src` | `'none'` | Refus par défaut |
| `script-src` | `'nonce-…' 'strict-dynamic'` | Seuls les scripts au nonce, et ceux qu'ils chargent |
| `style-src` | `'self' 'nonce-…'` | Feuilles du site et styles générés par Next.js |
| `img-src`, `font-src` | `'self'` | Aucune ressource tierce |
| `connect-src` | `'self'` + origine de l'API | Une donnée volée ne part pas vers un tiers |
| `object-src`, `base-uri`, `frame-ancestors` | `'none'` | Plugins, `<base>` injectée, détournement de clic |
| `form-action` | `'self'` | Un formulaire ne poste que vers le site |
| `upgrade-insecure-requests` | présente | Toute ressource HTTP passe en HTTPS |

Sous `next dev` seulement, React exige `'unsafe-eval'` (piles d'erreurs) et Next.js des styles en
ligne (`'unsafe-inline'`) : ces assouplissements n'existent jamais dans un build de production.

> **Sécurité.** `requestHeaders.set(...)` **remplace** toute CSP envoyée par le client : sans cela,
> un attaquant pourrait imposer son propre nonce au rendu. Un test le vérifie.

Le `matcher` du proxy exclut seulement `_next/static`, `_next/image` et `favicon.ico`. L'exemple
de la documentation exclut aussi les préchargements (`next-router-prefetch`) ; on ne le fait pas :
aucune réponse rendue ne doit sortir sans la CSP du serveur.

**Conséquence :** une page générée au build n'aurait pas de nonce. `src/app/layout.tsx` appelle
`await connection()`, ce qui impose le rendu à chaque requête pour toute l'application. Le build
doit afficher `ƒ (Dynamic)` pour chaque route.

> **Rappel.** Le proxy ne prend **aucune décision d'autorisation** : un contrôle d'accès dans le
> middleware de Next.js a déjà été contourné (CVE-2025-29927). Seule l'API fait foi.

## Étape 3 — Les en-têtes fixes

`src/security/security-headers.ts` produit les en-têtes indépendants de la requête, appliqués par
`headers()` de `next.config.ts` à **toutes** les routes, fichiers statiques compris :

| En-tête | Valeur | Ce qu'il empêche |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | Un texte interprété comme script |
| `Referrer-Policy` | `no-referrer` | La fuite d'une adresse vers un autre site |
| `X-Frame-Options` | `DENY` | L'affichage dans un cadre (anciens navigateurs) |
| `Permissions-Policy` | caméra, micro, géolocalisation… `=()` | L'usage de fonctions inutiles |
| `Cross-Origin-Opener-Policy` | `same-origin` | Une fenêtre tierce gardant une référence à la nôtre |
| `Cross-Origin-Resource-Policy` | `same-origin` | L'inclusion de nos ressources par un autre site |
| `Strict-Transport-Security` | 2 ans, sous-domaines (production) | Toute connexion en clair ensuite |

La CSP n'y figure pas : posée deux fois, les deux politiques s'appliqueraient ensemble et la plus
fermée bloquerait les scripts au nonce.

Dans `next.config.ts`, deux options complètent : `poweredByHeader: false` (pas d'en-tête
`X-Powered-By: Next.js`) et `productionBrowserSourceMaps: false` (le code source n'est pas publié).
Les pages dynamiques reçoivent déjà `Cache-Control: private, no-cache, no-store` de Next.js : un
cache ne resservira jamais une page, ni donc son nonce.

## Étape 4 — Mise en page et pages d'erreur

- `layout.tsx` : `<html lang="fr">`, titre et description par `metadata`.
- `not-found.tsx` : message générique, sans citer l'adresse demandée.
- `error.tsx` et `global-error.tsx` : composants client (obligatoire pour une frontière
  d'erreur), qui affichent `ErrorMessage` et un bouton « Réessayer » relié à `retry` (stable depuis
  Next.js 16.3). **L'erreur reçue n'est jamais lue** : en développement, elle porte le message
  d'origine, qui pourrait révéler une requête SQL ou un chemin interne.
- `global-error.tsx` remplace la mise en page racine : il fournit son propre `<html lang="fr">`.

Les apostrophes du texte sont typographiques (`’`) : la règle `react/no-unescaped-entities` refuse
l'apostrophe droite dans le JSX, et c'est la bonne typographie française.

## Étape 5 — Tests

Vitest reste en environnement `node`. Les composants sont rendus en HTML avec
`renderToStaticMarkup` de `react-dom/server`, sans DOM simulé ni dépendance de plus.
`vitest.config.ts` déclare l'alias `@` → `src`, que Vite ne lit pas dans `tsconfig.json`.

| Fichier | Ce qu'il prouve |
|---|---|
| `config/public-config.test.ts` | HTTPS imposé, schéma `javascript:`, identifiants, chemin refusés ; variable inconnue refusée sans afficher sa valeur |
| `security/content-security-policy.test.ts` | Nonce de 128 bits jamais répété ; pas d'`unsafe-*` en production |
| `security/security-headers.test.ts` | Chaque en-tête présent ; HSTS seulement en production |
| `proxy.test.ts` | Même CSP sur requête et réponse ; CSP du client ignorée ; nonce neuf ; `matcher` |
| `app/pages.test.tsx` | Aucune page d'erreur n'affiche le détail d'une exception |

L'utilitaire `unstable_doesMiddlewareMatch` de Next.js n'est pas utilisé : ses déclarations de
types sont cassées en 16.4.6, ce que `skipLibCheck: false` révèle. Le test compile le `matcher`
en expression régulière ancrée, comme le fait Next.js.

Une valeur d'attaque dans un test (`javascript:alert(1)`) déclenche `no-script-url` : l'exception
est locale et justifiée (`// eslint-disable-next-line no-script-url -- …`).

## Vérification

```bash
pnpm run lint && pnpm run typecheck && pnpm run format:check && pnpm run test
NEXT_PUBLIC_API_BASE_URL=https://api.taskhandler.localhost:8443 pnpm run build
NEXT_PUBLIC_API_BASE_URL=https://api.taskhandler.localhost:8443 pnpm exec next start -p 3100
curl -sI http://localhost:3100/
```

À constater : un build sans variable, en HTTP ou avec `NEXT_PUBLIC_SECRET_KEY` échoue ; deux
requêtes reçoivent deux nonces ; chaque `<script>` de la page porte `nonce=` ; aucune source map
dans `.next/static` ; `/n-existe-pas` répond `404`.

**Preuve de fin de phase**, dans un vrai navigateur : créez une page sonde temporaire
`src/app/csp-probe/page.tsx` contenant `<script>` qui modifie le titre `<h1>`, construisez,
ouvrez `/csp-probe`. Le titre ne doit pas changer et la console doit signaler une violation de
`script-src`. Supprimez ensuite la sonde.

Résultat obtenu dans Chrome : le `<h1>` reste « Script bloqué » ; la seule balise sans nonce est
celle de la sonde, tandis que les 15 scripts de Next.js, qui portent le nonce, se sont exécutés
(`self.__next_f` existe). Le blocage vient donc bien de la CSP, pas d'une panne générale.

> **Limite à connaître.** `'strict-dynamic'` autorise tout script **créé par du JavaScript**
> (`document.createElement("script")`), quel que soit son auteur. Un script ajouté ainsi depuis la
> console s'exécute. Ce n'est pas une brèche : pour l'ajouter, un attaquant doit déjà exécuter du
> code. La CSP bloque l'étape d'avant, l'injection de HTML. D'où la règle complémentaire : aucune
> injection de HTML (`dangerouslySetInnerHTML`, `innerHTML`), interdite par ESLint dès la phase 0.

## Ce que cette phase ne fait pas

Pas de Trusted Types (`require-trusted-types-for`) : leur compatibilité avec React et Next.js sera
évaluée en phase 9, avec Playwright. Pas de CI (phase 2), pas d'appel à l'API (phase 4).
