# Phase 3 — Intégration continue

## Objectif

Aucun changement n'est accepté sans passer les contrôles de qualité et de sécurité.

**Résultat attendu :** une dépendance vulnérable ou un test en échec bloque le pipeline.

Le pipeline complet et commenté se trouve dans `.github/workflows/ci.yml`. Ce guide en explique
les décisions. L'outil retenu est **GitHub Actions**.

## Étape 1 — Ajouter les deux outils manquants

Depuis `backend/` :

```bash
uv add --dev coverage pip-audit
```

Ils entrent dans `uv.lock` avec leurs empreintes : la CI utilise exactement les versions relues,
au lieu de télécharger la dernière version disponible à chaque exécution.

Dans `pyproject.toml`, configurez la couverture :

```toml
[tool.coverage.run]
branch = true
source = ["app", "migrations"]

[tool.coverage.report]
fail_under = 95
```

`branch = true` exige de tester les deux sens de chaque `if`. Le seuil a été fixé juste sous la
valeur mesurée (96 %) : il empêche de régresser sans être un objectif artificiel.

> **Bonne pratique.** La couverture indique ce qui n'est **pas** testé, elle ne prouve pas que le
> reste est bien testé. Un test sans assertion augmente le pourcentage et ne vérifie rien.

## Étape 2 — La structure du pipeline

Créez `.github/workflows/ci.yml` avec trois tâches indépendantes :

| Tâche | Contrôles |
|---|---|
| `quality` | `ruff check`, `ruff format --check`, `mypy` |
| `tests` | PostgreSQL, migrations, `alembic check`, `pytest`, seuil de couverture |
| `audit` | `pip-audit`, `gitleaks` sur tout l'historique |

Trois tâches plutôt qu'une seule : une erreur de formatage n'empêche pas de savoir si les tests
passent, et chaque tâche peut devenir un contrôle obligatoire distinct.

## Étape 2 bis — Deux branches

| Branche | Rôle | Qui y écrit |
|---|---|---|
| `daniel_dev` | Travail en cours, commit après commit | Le développeur, par `git push` |
| `main` | Version validée, toujours saine | Personne directement : seulement une pull request |

```yaml
on:
  push:
    branches: [main, daniel_dev]
  pull_request:
    branches: [main]
```

Le push sur `daniel_dev` donne un retour rapide ; la pull request vérifie ensuite le code tel
qu'il sera **après** fusion dans `main`. Une CI sans branche protégée ne fait qu'informer.

## Étape 3 — Sécuriser le pipeline lui-même

Une CI exécute du code avec des droits : c'est une cible de choix.

**Permissions minimales.** Aucun droit par défaut, puis seulement la lecture du code :

```yaml
permissions: {}
jobs:
  tests:
    permissions:
      contents: read
```

**Actions épinglées par empreinte.** Un tag comme `v7` peut être déplacé vers un code malveillant :

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

Pour retrouver l'empreinte d'une version :

```bash
git ls-remote --tags https://github.com/actions/checkout.git
```

Autres précautions appliquées :

| Réglage | Risque évité |
|---|---|
| `persist-credentials: false` | Le jeton GitHub reste sur le disque, à portée des étapes suivantes |
| `pull_request`, jamais `pull_request_target` | Le code d'un tiers s'exécute avec les droits du dépôt |
| `enable-cache: false` | Une branche empoisonne le cache utilisé par la suivante |
| `timeout-minutes` | Une tâche bloquée consomme le quota indéfiniment |
| Image et runner à version fixe | Le pipeline change sans qu'aucun commit ne le montre |

> **Sécurité : injection dans le pipeline.** N'écrivez jamais `${{ github.event... }}` dans un bloc
> `run:` : un titre de pull request contenant une commande shell serait exécuté tel quel.

## Étape 4 — PostgreSQL et les secrets dans la CI

La tâche `tests` réutilise les fichiers du développement au lieu de redéfinir un service :

```yaml
- run: scripts/generate-dev-env.sh
- run: docker compose --env-file .env.postgres up --detach --wait
- run: uv run --locked alembic upgrade head
```

La CI teste ainsi la même image, avec les mêmes rôles et les mêmes droits. Un bloc `services:`
serait démarré avant la récupération du code : il ne pourrait pas exécuter `init-roles.sh`.

Les mots de passe sont générés à chaque exécution, puis disparaissent avec la machine. **Aucun
secret GitHub n'est configuré** : ce qui n'existe pas ne peut pas fuiter.

## Étape 5 — Vérifier les migrations

```bash
uv run --locked alembic check
```

Cette commande compare les modèles au schéma migré, et échoue si une migration manque. Sans elle,
un modèle modifié sans migration passerait la CI, puis casserait en production.

## Étape 6 — L'audit

**Dépendances.** On audite le fichier de verrouillage exporté, avec les empreintes, donc les
versions exactes, outils de développement compris :

```bash
uv export --locked --no-emit-project --quiet --output-file requirements.txt
uv run --locked pip-audit --disable-pip --require-hashes --requirement requirements.txt
```

**Secrets.** Le hook pre-commit ne voit que les changements locaux et peut être contourné avec
`--no-verify`. La CI analyse donc **tout l'historique**, d'où `fetch-depth: 0` :

```bash
docker run --rm --volume "$PWD:/repo:ro" ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:... git --redact --no-banner /repo
```

Un secret retiré dans un commit suivant reste lisible dans l'historique : il doit être **renouvelé**.
`--redact` évite de recopier le secret trouvé dans les journaux de la CI.

## Étape 7 — Valider sans attendre GitHub

`actionlint` détecte les erreurs de syntaxe du pipeline. C'est un contrôle ponctuel, lancé depuis
son image, donc aucune dépendance de plus pour le projet :

```bash
docker run --rm -v "$PWD:/repo:ro" -w /repo rhysd/actionlint .github/workflows/ci.yml
```

Prouvez ensuite que le pipeline **bloque** : sur une copie jetable, introduisez un défaut, exécutez
la tâche concernée et vérifiez qu'elle échoue à la bonne étape.

| Défaut injecté | Étape qui doit échouer |
|---|---|
| Import inutile | Analyse statique |
| Test en échec | Tests |
| `jinja2==3.1.2` | Audit des dépendances |
| Faux jeton commité, puis retiré | Secrets dans l'historique |
| Modèle sans migration | `alembic check` |
| Fichier sans test | Seuil de couverture |

Un contrôle qu'on n'a jamais vu échouer n'est pas un contrôle.

## Étape 8 — Rendre le blocage réel sur GitHub

Un pipeline rouge n'interdit rien tant que `main` n'est pas protégée. Dans **Settings → Rules →
Rulesets**, créez une règle ciblant `main` :

- exiger une pull request avant la fusion, et interdire le push forcé et la suppression ;
- exiger les contrôles `quality`, `tests` et `audit`, sur une branche à jour ;
- ne laisser aucune exception (« bypass list » vide), administrateurs compris.

Ces noms de contrôles n'apparaissent qu'après une première exécution du pipeline.

> **Confidentialité.** Sur un dépôt public, l'adresse e-mail de chaque commit est publique. Utilisez
> l'adresse `…@users.noreply.github.com` (Settings → Emails) avant de pousser.

## À retenir

- La CI est du code qui s'exécute avec des droits : épinglez, restreignez, ne confiez aucun secret
  dont elle n'a pas besoin.
- Le développement et la CI partagent les mêmes fichiers : ils ne peuvent pas diverger.
- Les empreintes figées ne se mettent pas à jour seules : il faut les renouveler régulièrement.

**Normes concernées :** OWASP Top 10 CI/CD Security Risks (CICD-SEC-3, 4 et 8) ; OWASP API Security
Top 10 2023, API8 (configuration) ; OpenSSF Scorecard, actions épinglées et permissions minimales.
