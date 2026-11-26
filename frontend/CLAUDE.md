# CLAUDE.md — frontend

Règles propres au frontend Next.js de TaskHandler. Elles **complètent** le [CLAUDE.md](../CLAUDE.md)
racine, qui reste entièrement applicable (règles de travail, format du code, secrets, commits).

Lire [roadmap.md](roadmap.md) pour l'avancement, puis `docs/steps/step-x.md` de la phase en cours.

## Pile et architecture

- Next.js (App Router), React, TypeScript `strict` avec `noUncheckedIndexedAccess`.
- Dépendances gérées par **pnpm** uniquement : jamais `npm install`, `yarn` ni Poetry.
- Le navigateur appelle directement l'API `/api/v1`. Le serveur Next.js ne sert que l'application :
  il ne reçoit, ne relaie ni ne stocke aucun jeton ni aucune donnée utilisateur.
- Aucune Server Action, aucun Route Handler qui lit ou modifie une donnée utilisateur.
- Les pages authentifiées sont des composants client ; un composant serveur ne rend que du contenu
  public.

## Format obligatoire du code

Toute fonction, tout composant React et tout hook, y compris de test, est précédé des trois
étiquettes du CLAUDE.md racine, en commentaires `//` :

```tsx
// requires : `task` provient d'une réponse de l'API déjà validée.
// assigns  : rien (composant sans effet de bord).
// ensures  : affiche le titre comme du texte brut ; aucun HTML n'est interprété.
export function TaskTitle({ task }: { task: PlanningEntry }): React.JSX.Element {
  // React échappe le texte : c'est la défense principale contre l'injection de script.
  return <span>{task.title}</span>;
}
```

- Typage complet : `any` interdit. `@ts-ignore`, `@ts-expect-error` et `eslint-disable` exigent
  une justification en ligne. Préférer `unknown` puis une validation.
- Commentaires abondants en français, surtout pour expliquer les choix de sécurité.

## Session et jetons

- Jeton d'accès : **en mémoire JavaScript uniquement**. Jamais dans `localStorage`,
  `sessionStorage`, IndexedDB, un cookie lisible, une URL ou un journal.
- Jeton de renouvellement : cookie `HttpOnly` posé par l'API, jamais lisible ni manipulé par le
  frontend. `credentials: "include"` est réservé aux routes d'authentification qui l'exigent.
- Connexion et renouvellement portent l'en-tête anti-CSRF `X-CSRF-Protection: 1` exigé par l'API.
- Renouveler avant expiration à partir de `expires_in`, jamais en décodant le JWT pour s'y fier.
- Un seul renouvellement à la fois, tous onglets confondus (Web Locks) : deux rotations
  concurrentes déclenchent la détection de réutilisation et coupent la session.
- Déconnexion, suppression du compte ou révocation : effacer tout état en mémoire et prévenir les
  autres onglets (BroadcastChannel).
- Jetons à usage unique reçus par e-mail : lus dans le fragment `#token=…`, effacés aussitôt de
  l'URL avec `history.replaceState`, puis envoyés dans un corps `POST`.

## Autorisation

- Les gardes de routes côté client sont du **confort d'affichage**, pas un contrôle de sécurité :
  seule l'API décide. Ne jamais fonder une autorisation sur le middleware (ou `proxy`) de Next.js.
- Une réponse `404` de l'API s'affiche comme « introuvable », sans supposer l'existence de la
  ressource.

## Prévention des XSS

- Interdits : `dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function`, `setTimeout` avec
  une chaîne, rendu HTML ou Markdown d'un contenu saisi par un utilisateur.
- Une valeur issue de l'API ou de l'utilisateur ne va jamais dans `href`, `src`, `style` ou un
  gestionnaire d'événement sans validation de schéma d'URL par liste blanche (`https:` seulement).
- CSP stricte générée par requête avec un nonce : `default-src 'none'`, `script-src` à nonce et
  `'strict-dynamic'`, `connect-src` limité à l'API, `object-src`, `base-uri` et
  `frame-ancestors` à `'none'`. Jamais `'unsafe-inline'` ni `'unsafe-eval'` pour les scripts.

## Données et validation

- Les réponses de l'API sont **non fiables** (OWASP API10) : validées à l'exécution avant usage.
- Types générés depuis le contrat OpenAPI : jamais de type d'API écrit à la main en double.
- Les formulaires reprennent les limites de l'API (longueurs, intervalles, 31 jours) pour guider
  l'utilisateur ; l'API reste la seule validation qui fait foi.
- La grille n'affiche jamais la description d'une tâche : elle est chargée au clic.
- Erreurs `application/problem+json` : message générique pour l'utilisateur, jamais le détail
  technique brut. `429` : respecter `Retry-After`, sans nouvelle tentative automatique en boucle.
- Dates : jours au format ISO 8601, heures de 0 à 23, dans le fuseau local de l'utilisateur.

## Configuration et secrets

- Toute variable `NEXT_PUBLIC_*` est **publique** : elle finit dans le code envoyé au navigateur.
  Aucun secret n'a sa place dans le frontend.
- Configuration validée au démarrage ; l'adresse de l'API est en HTTPS hors développement.
- Source maps non publiées en production, télémétrie de Next.js désactivée.
- Aucun script, police ou outil d'analyse chargé depuis un domaine tiers.

## En-têtes HTTP

- `Content-Security-Policy` (ci-dessus), `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, `Permissions-Policy` restrictive, HSTS en production.
- `poweredByHeader: false` : ne pas annoncer le framework.

## Journaux

- Pas de `console.log` en production. Ne jamais journaliser un jeton, un mot de passe, une
  adresse e-mail ni le contenu d'une tâche.

## Accessibilité

- Cible WCAG 2.2 AA : libellés de formulaires, focus visible, contraste, navigation clavier
  complète, en particulier dans la grille.

## Dépendances

- Nouvelle dépendance : justification et validation préalables, nom exact vérifié
  (typosquatting), maintenance et vulnérabilités connues contrôlées.
- `pnpm-lock.yaml` versionné ; installation `--frozen-lockfile` en CI.
- Scripts d'installation des paquets bloqués, sauf liste blanche explicite et justifiée.
- Délai minimal avant d'adopter une version fraîchement publiée (attaques de chaîne
  d'approvisionnement).
- `pnpm audit` doit passer avant de terminer un travail. Next.js et React sont tenus à jour des
  correctifs de sécurité sans délai.

## Tests de sécurité obligatoires

- Aucun jeton dans `localStorage`, `sessionStorage` ni `document.cookie` à aucun moment.
- Une charge XSS dans un titre ou une description s'affiche comme du texte.
- Les en-têtes de sécurité, dont la CSP à nonce, sont présents sur chaque page.
- Le fragment `#token=…` disparaît de l'URL après lecture.
- Deux utilisateurs ne voient jamais les données l'un de l'autre (tests de bout en bout).
- Toute correction d'un défaut de sécurité commence par un test qui le reproduit.

## Avant de déclarer un travail terminé

1. Les contrôles disponibles à cette phase passent : ESLint, `tsc --noEmit`, Vitest, Playwright,
   `pnpm audit`, build de production, gitleaks.
2. Chaque nouvelle fonction, composant ou hook porte `requires`, `assigns` et `ensures`.
3. Aucun secret ni donnée sensible dans le code, le bundle, les journaux ou l'URL.
4. La roadmap du frontend est à jour et les limites de taille des fichiers sont respectées.
5. Indiquer ce qui a été vérifié, ce qui ne l'a pas été, puis **demander avant tout commit**.
