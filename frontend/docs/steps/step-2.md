# Phase 2 du frontend — Intégration continue

## Objectif

Les hooks pre-commit protègent le poste du développeur, mais on peut les contourner
(`git commit --no-verify`) ou les oublier. La CI, elle, s'exécute pour chaque push et chaque pull
request vers `main` : c'est le contrôle qui fait foi. Le frontend y entre avec les mêmes exigences
que le backend.

**Résultat attendu :** une dépendance vulnérable ou un test frontend en échec bloque la pull
request.

**Prérequis :** phase 1 terminée. On modifie `.github/workflows/ci.yml` (racine) et
`frontend/vitest.config.ts`.

## Étape 1 — Mesurer la couverture et fixer un seuil

Une nouvelle dépendance, validée : `@vitest/coverage-v8`, **à la même version exacte que Vitest**
(ici 5.1.1), car ce sont deux morceaux d'un même outil.

```bash
pnpm add --save-dev @vitest/coverage-v8@5.1.1
```

Dans `vitest.config.ts`, le bloc `coverage` :

| Réglage | Valeur | Pourquoi |
|---|---|---|
| `provider` | `"v8"` | Mesure par le moteur de Node.js, sans réécrire le code |
| `include` | `["src/**/*.{ts,tsx}"]` | Mesure **tout** `src/` |
| `exclude` | tests et `*.d.ts` | Rien d'exécutable à mesurer |
| `thresholds` | 95 % lignes, instructions, fonctions, **branches** | Même seuil que le backend |

> **Piège.** Sans `include`, Vitest ne mesure que les fichiers qu'un test importe. Un module que
> personne ne teste n'apparaît alors nulle part et ne fait pas baisser le taux : la couverture
> affichée est flatteuse et fausse. C'est ainsi que `layout.tsx` et `page.tsx` étaient invisibles.

Le script `test:coverage` (`vitest run --coverage`) échoue sous le seuil. Vérifiez-le : un module
sonde non testé fait tomber les quatre taux sous 95 % et la commande sort en code 1.

La mesure a révélé trois manques, désormais testés : le message d'erreur d'un schéma interdit en
développement, la page d'accueil, et la mise en page racine. Celle-ci appelle `connection()`, qui
exige une vraie requête HTTP : le test le remplace par une promesse résolue avec `vi.mock`, car il
porte sur le document produit, pas sur le mode de rendu.

## Étape 2 — La tâche `frontend`

Elle reprend les principes de la phase 3 du backend :

- **droits minimaux** : la permission globale reste `{}`, la tâche déclare `contents: read` ;
- **actions épinglées par empreinte de commit**, jamais par étiquette (`v7` peut être déplacée
  vers du code malveillant, une empreinte non) ; la version lisible est en commentaire ;
- **`persist-credentials: false`** : le jeton GitHub ne reste pas sur le disque ;
- **`defaults.run.working-directory: frontend`** au niveau de la tâche, qui remplace `backend`.

| Étape | Commande | Rôle |
|---|---|---|
| Node.js | `actions/setup-node`, `node-version-file: frontend/.nvmrc` | Version exacte, comme en local |
| pnpm | `npm install --global "$(node --print '…packageManager')"` | Version lue dans `package.json` |
| Dépendances | `pnpm install --frozen-lockfile` | Refuse tout écart avec le lockfile |
| Qualité | `lint`, `format:check`, `typecheck` | Les mêmes contrôles que les hooks |
| Tests | `test:coverage` | Tests et seuil de 95 % |
| Build | `pnpm run build` | Le code compile vraiment en production |
| Source maps | `test -z "$(find .next/static -name '*.map' …)"` | Le code source n'est pas publié |

**Pourquoi pas `pnpm/action-setup` ?** Chaque action tierce est un fournisseur de plus à qui
l'on confie l'exécution de code dans la CI. npm, déjà présent avec Node.js, vérifie l'empreinte
publiée par le registre ; la version vient du champ `packageManager`, unique source de vérité.

Le build reçoit `NEXT_PUBLIC_API_BASE_URL=https://api.ci.invalid` : le domaine `.invalid` est
réservé et ne résout jamais. La configuration est validée pour de vrai, mais rien n'est publié.
`NEXT_TELEMETRY_DISABLED=1` est posé au niveau de la tâche.

## Étape 3 — Pas de cache partagé

La roadmap envisageait de mettre en cache le magasin pnpm. On y renonce, pour la même raison que
le backend (`enable-cache: false` pour uv) : une branche, ou une pull request, pourrait déposer
dans le cache un paquet modifié que la branche suivante réutiliserait. pnpm vérifie les empreintes
du lockfile, mais un cache empoisonné reste une surface d'attaque inutile.

`actions/setup-node` active de lui-même un cache dans certains cas : `package-manager-cache: false`
le désactive explicitement. Coût : une vingtaine de secondes de téléchargement par exécution.

> **Bonne pratique.** La sécurité prime sur la vitesse : un pipeline lent mais sain vaut mieux
> qu'un pipeline rapide dont on ne maîtrise pas les entrées.

## Étape 4 — La tâche `frontend-audit`

`pnpm audit --audit-level low` lit les versions exactes du lockfile, outils de développement
compris, et échoue sur **toute** vulnérabilité connue, quel que soit son niveau. Une tâche séparée
rend la cause d'un échec lisible d'un coup d'œil : une nouvelle alerte publiée sur une dépendance
n'est pas un test cassé.

En cas d'alerte : mettre à jour la dépendance (en respectant le délai de 7 jours, ou par une
exception `minimumReleaseAgeExclude` justifiée s'il s'agit d'un correctif urgent).

## Étape 5 — Protéger `main`

Les tâches s'exécutent à chaque push, sans filtre de chemins : un filtre combiné à une
vérification obligatoire laisserait une pull request bloquée en attente d'une tâche jamais lancée.

Dans les réglages GitHub du dépôt (Branches → règle de `main`), ajoutez `frontend` et
`frontend-audit` aux vérifications obligatoires. Cette étape se fait à la main : sans elle, la CI
signale l'échec mais n'empêche pas la fusion.

## Vérification

Simulez la CI sur une copie propre de `frontend/` (fichiers suivis par Git uniquement, sans
`node_modules`, `.next` ni `.env.local`) :

```bash
pnpm install --frozen-lockfile
pnpm run lint && pnpm run format:check && pnpm run typecheck && pnpm run test:coverage
NEXT_PUBLIC_API_BASE_URL=https://api.ci.invalid pnpm run build
test -z "$(find .next/static -name '*.map' -print -quit)"
pnpm audit --audit-level low
```

Après le push, vérifiez dans l'onglet Actions que `frontend` et `frontend-audit` sont vertes.

## Ce que cette phase ne fait pas

Pas de tests de bout en bout (Playwright, phase 9), pas d'image Docker ni de SBOM du frontend
(phase 10).
