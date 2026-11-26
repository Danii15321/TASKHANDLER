# Phase 0 du frontend — Socle Next.js et outillage qualité

## Objectif

Avant d'écrire le moindre écran, on construit le filet de sécurité du frontend : des dépendances
installées de façon reproductible et méfiante, un typage aussi strict que `mypy --strict`, et des
hooks qui refusent une erreur de type, une règle de sécurité violée ou un secret.

Dans l'écosystème npm, un paquet compromis s'exécute **dès son installation**, avant toute relecture.
La plupart des attaques récentes (comptes de mainteneurs volés, scripts `postinstall` malveillants)
visent ce moment. On le verrouille donc en premier.

**Résultat attendu :** les hooks bloquent une erreur de type, une règle ESLint violée et un faux
secret.

**Prérequis :** la phase 11 du backend terminée, Git, [nvm](https://github.com/nvm-sh/nvm). Les
commandes partent de `frontend/`, sauf mention contraire.

## Étape 1 — Épingler Node.js et pnpm

On choisit la LTS **active** de Node.js (26 en novembre 2026) : c'est celle qui recevra le plus
longtemps les correctifs de sécurité. Poetry est exclu : il ne gère que des paquets Python.

```bash
nvm install 26
printf '26.13.1\n' > .nvmrc
nvm use
npm install --global pnpm@12.22.0
```

`.nvmrc` fixe la version exacte, comme `backend/.python-version`. Dans `package.json`, deux champs
complètent l'épinglage :

- `"packageManager": "pnpm@12.22.0"` : la version de pnpm attendue par le projet ;
- `"engines": { "node": ">=26.13.1 <27", "pnpm": "12.22.0" }` : refus d'une autre version.

Corepack n'est plus livré avec Node.js depuis la version 25 : pnpm s'installe donc via npm, et
c'est le réglage `pmOnFail: error` (étape 2) qui garantit que la bonne version tourne.

## Étape 2 — Configurer pnpm contre les attaques de chaîne d'approvisionnement

Depuis pnpm 11, les réglages vivent dans `pnpm-workspace.yaml` ; `.npmrc` ne sert plus qu'à
l'authentification. Chaque réglage du fichier est commenté ; voici leur rôle :

| Réglage | Valeur | Ce qu'il empêche |
|---|---|---|
| `minimumReleaseAge` | `10080` (7 jours) | Installer une version malveillante avant son retrait du registre |
| `minimumReleaseAgeStrict` | `true` | Se rabattre silencieusement sur une version trop récente |
| `trustPolicy` | `no-downgrade` | Accepter une version publiée sans la provenance des précédentes |
| `blockExoticSubdeps` | `true` | Une dépendance transitive tirée d'un dépôt Git ou d'une URL |
| `strictDepBuilds` + `allowBuilds: {}` | — | Exécuter un script `postinstall` non revu |
| `engineStrict`, `strictPeerDependencies` | `true` | Une incompatibilité ignorée parce que simple avertissement |
| `autoInstallPeers` | `false` | Une dépendance paire ajoutée sans être déclarée |
| `pmOnFail` | `error` | Télécharger en douce une autre version de pnpm |
| `verifyDepsBeforeRun` | `error` | Lancer un contrôle sur des dépendances désynchronisées |
| `saveExact` | `true` | Une mise à jour implicite via une plage `^` |

> **Sécurité.** Un correctif urgent de Next.js ou React ne doit pas attendre 7 jours : on l'ajoute
> à `minimumReleaseAgeExclude`, nommé et justifié en commentaire, puis on retire l'exception.

**Piège rencontré :** `saveExact` ne s'applique pas à un paquet demandé avec une plage
(`pnpm add eslint@10` écrit `^10.15.0`). Vérifiez `package.json` après chaque ajout et retirez le `^`.

## Étape 3 — Installer Next.js sans gabarit

`create-next-app` génère des fichiers d'exemple, des styles et parfois Tailwind : tout ce que la
règle « aucun scaffold » interdit. On écrit donc `package.json` à la main (nom, `private: true`,
`type: module`, scripts), puis :

```bash
pnpm add next react react-dom
pnpm add --save-dev typescript@6 @types/node@26 @types/react @types/react-dom eslint@10 \
  eslint-config-next typescript-eslint prettier vitest vite
```

Choix de versions, tous vérifiés avant installation :

- **TypeScript 6**, pas 7 : typescript-eslint n'accepte pas encore la réécriture native (`<6.1.0`).
- **ESLint 10** : la 9 est en fin de vie depuis août 2026. `eslint-config-next` l'accepte et
  adapte lui-même les anciens plugins (`fixupPluginRules`). Seuls eslint-plugin-react, -import et
  -jsx-a11y déclarent encore une plage trop étroite : une exception `peerDependencyRules` limitée à
  ces trois paquets et à `eslint@10` le documente, avec sa condition de retrait.
- **vite** est déclaré explicitement : c'est une dépendance paire de Vitest 5.

Le code de départ se limite à `src/app/layout.tsx` (`<html lang="fr">`) et `src/app/page.tsx`, chacun
précédé de `requires`, `assigns` et `ensures`. `next.config.ts` n'active que `reactStrictMode` ; les
en-têtes de sécurité arrivent en phase 1, avec leurs tests.

Complétez le `.gitignore` racine : `node_modules/`, `.next/`, `next-env.d.ts`, `*.tsbuildinfo`.
`pnpm-lock.yaml`, lui, est **versionné** : c'est lui qui rend l'installation reproductible.

## Étape 4 — TypeScript en mode strict

Dans `tsconfig.json`, au-delà de `"strict": true` :

| Option | Pourquoi |
|---|---|
| `noUncheckedIndexedAccess` | `liste[i]` vaut `T \| undefined` : l'absence doit être traitée |
| `exactOptionalPropertyTypes` | Absent et `undefined` sont distincts, comme `extra="forbid"` côté API |
| `noImplicitReturns`, `noFallthroughCasesInSwitch` | Chaque branche est explicite |
| `noPropertyAccessFromIndexSignature` | Une clé dynamique se lit avec `obj["clé"]`, visiblement |
| `skipLibCheck: false` | Les types des dépendances sont vérifiés aussi |
| `allowJs: false` | Aucun fichier non typé dans le code de l'application |

TypeScript ne produit rien (`noEmit`) : Next.js compile. Le script `typecheck` lance d'abord
`next typegen`, qui génère `next-env.d.ts` et les types de routes : la vérification fonctionne
donc aussi sur un dépôt fraîchement cloné, sans build préalable.

## Étape 5 — ESLint, accessibilité et règles de sécurité

`eslint.config.mjs` empile trois couches :

1. `eslint-config-next/core-web-vitals` et `/typescript` : React, hooks, imports, Next.js ;
2. `tseslint.configs.strictTypeChecked` et `stylisticTypeChecked`, avec `projectService` : ces
   règles lisent les types (`no-explicit-any`, `no-unsafe-*`, promesses non attendues…) ;
3. nos interdictions, qui traduisent `frontend/CLAUDE.md` en contrôles automatiques.

| Règle | Ce qu'elle bloque |
|---|---|
| `no-eval`, `no-new-func`, `no-script-url` | Exécuter une chaîne comme du code |
| `react/no-danger`, `react/jsx-no-script-url` | `dangerouslySetInnerHTML`, liens `javascript:` |
| `react/jsx-no-target-blank`, `react/iframe-missing-sandbox` | Fuite de `window.opener`, cadre non isolé |
| `no-restricted-globals` / `-properties` | `localStorage`, `sessionStorage`, `indexedDB`, `document.cookie`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write` |
| `no-console` | Un journal de navigateur contenant un jeton |
| `@typescript-eslint/ban-ts-comment` | `@ts-ignore` ; `@ts-expect-error` sans justification |
| `explicit-function-return-type` | Une fonction sans type de retour déclaré |

Next.js n'active que quelques règles d'accessibilité. On ajoute le jeu **strict** de jsx-a11y en le
lisant dans le plugin déjà chargé par Next.js (même instance, aucune dépendance de plus).
`reportUnusedDisableDirectives: "error"` fait échouer toute exception devenue inutile.

> **Bonne pratique.** Une configuration non testée est une configuration supposée. Écrivez un
> fichier sonde qui viole chaque règle, lancez `pnpm exec eslint` dessus, vérifiez que chaque règle
> apparaît, puis supprimez la sonde. C'est ainsi qu'on a découvert l'accessibilité incomplète.

eslint-plugin-security n'est pas retenu : il cible Node.js et produit surtout des faux positifs dans
un code React.

## Étape 6 — Formatage, télémétrie et premier test

- **Prettier**, `printWidth: 100` comme ruff. `.prettierignore` exclut les fichiers générés,
  `pnpm-lock.yaml` et `*.md` (documentation mise en forme à la main).
- **Télémétrie** : Next.js envoie par défaut des statistiques d'usage. Chaque script `next` est
  préfixé par `NEXT_TELEMETRY_DISABLED=1`, versionné dans `package.json` plutôt que dans un réglage
  propre à une machine.
- **Vitest** : `vitest.config.ts` limite la recherche à `src/` en environnement `node`. Le premier
  test, `src/toolchain.test.ts`, vérifie que Node.js est exactement la version de `.nvmrc`, comme
  `backend/tests/test_toolchain.py` le fait pour Python.

## Étape 7 — Hooks pre-commit

Dans `.pre-commit-config.yaml` (racine), trois hooks locaux `language: system`, déclenchés par
`files: ^frontend/` et `pass_filenames: false` (une modification peut casser un autre fichier) :
`pnpm --dir frontend run lint`, `run format:check` et `run typecheck`. gitleaks couvre déjà tout le
dépôt. pnpm doit être dans le `PATH` du hook : avec nvm, lancez `nvm use` avant `git commit`.

## Vérification

```bash
pnpm install --frozen-lockfile
pnpm run lint
pnpm run typecheck
pnpm run format:check
pnpm run test
pnpm run build
pnpm audit
```

Preuves de fin de phase, chacune avec une sonde supprimée ensuite :

1. une affectation `const n: number = "texte"` fait échouer le hook `tsc` ;
2. un appel à `localStorage.setItem` fait échouer le hook `eslint` ;
3. un faux jeton GitHub mis dans l'index fait échouer gitleaks (`RuleID: github-pat`).

## Ce que cette phase ne fait pas

Pas d'en-têtes de sécurité, de CSP ni de page d'erreur (phase 1), pas de CI (phase 2), pas
d'environnement DOM pour tester des composants : chacun arrive avec la phase qui en a besoin.
