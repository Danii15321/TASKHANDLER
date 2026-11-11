# Phase 0 — Dépôt et outillage qualité

## Objectif

Avant d'écrire la moindre fonctionnalité, on met en place un filet de sécurité : aucun secret ne
doit pouvoir entrer dans l'historique Git, et chaque ligne doit être vérifiée automatiquement.

Un secret poussé dans Git reste lisible dans l'historique, même après sa suppression : il faut
alors le renouveler. Il est bien moins coûteux d'empêcher l'erreur que de la réparer.

**Résultat attendu :** les hooks Git bloquent un faux secret et une erreur de type.

**Prérequis :** Git et [uv](https://docs.astral.sh/uv/). Les commandes partent de la racine du dépôt.

## Étape 1 — Initialiser Git et écrire le `.gitignore`

Lancez `git init`, mais ne committez rien avant la fin de cette phase. Créez `.gitignore` à la
racine. Le bloc le plus important concerne les secrets :

```gitignore
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
```

La ligne `!.env.example` crée une exception : ce modèle, sans vraie valeur, doit être versionné.
Ajoutez ensuite `.venv/`, `__pycache__/`, `*.py[cod]`, `.mypy_cache/`, `.pytest_cache/`,
`.ruff_cache/`, `.coverage`, `.coverage.*`, `htmlcov/`, `.idea/`, `.vscode/` et `.DS_Store`.

> **Sécurité.** Le `.gitignore` ne protège pas d'un secret collé dans un fichier versionné : c'est
> le rôle de gitleaks, à l'étape 5.

## Étape 2 — Créer le projet `backend/` avec uv

uv gère à la fois la version de Python, l'environnement virtuel et les dépendances.

```bash
mkdir backend
cd backend
uv python install 3.14.10
uv init --bare --name taskhandler-backend --python 3.14.10 --no-workspace
uv python pin 3.14.10
```

`--bare` ne crée que `pyproject.toml` : aucun code d'exemple inutile. Dans ce fichier, remplacez la
ligne `requires-python` par `requires-python = ">=3.14,<3.15"`.

Le fichier `.python-version` fixe la version exacte. On choisit le dernier correctif de la branche
3.14, car ces versions corrigent aussi des failles.

Ajoutez ensuite les outils, uniquement comme dépendances de développement :

```bash
uv add --dev ruff mypy pytest pre-commit
```

> **Bonne pratique.** Versionnez `uv.lock` : il fixe la version et l'empreinte de chaque paquet, ce
> qui rend l'installation reproductible et fait rejeter un paquet altéré.
>
> **Sécurité.** Vérifiez le nom exact de chaque paquet : le *typosquatting* consiste à publier un
> paquet malveillant sous un nom presque identique à celui d'un paquet connu.

## Étape 3 — Configurer ruff

Ruff regroupe un linter et un formateur. Ajoutez à la fin de `pyproject.toml` :

```toml
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "W", "F", "I", "N", "UP", "B", "S", "A", "C4", "DTZ", "T20", "SIM", "RET", "PTH", "ASYNC", "RUF"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]
```

Le code réel commente chaque famille de règles. Les plus importantes pour la sécurité :

| Règle | Ce qu'elle apporte |
|---|---|
| `S` (Bandit) | Détecte le SQL construit à la main, `shell=True`, `pickle`, `random` utilisé pour la sécurité |
| `DTZ` | Interdit les dates sans fuseau horaire, source classique d'erreurs d'expiration de jetons |
| `T20` | Interdit `print`, qui contournerait le filtrage des journaux |
| `ASYNC` | Détecte les appels bloquants qui figeraient toutes les requêtes |

`S101` interdit `assert`, que Python supprime avec l'option `-O` : un contrôle de sécurité écrit
ainsi disparaîtrait en production. On ne l'ignore que dans les tests, où `assert` est normal.
Toute autre exception doit rester rare et justifiée en commentaire.

## Étape 4 — Configurer mypy et pytest

```toml
[tool.mypy]
strict = true
warn_unreachable = true

[tool.pytest.ini_options]
minversion = "9.0"
testpaths = ["tests"]
addopts = ["--strict-markers", "--strict-config", "--import-mode=importlib", "-ra"]
xfail_strict = true
filterwarnings = ["error"]
```

Le mode strict impose d'annoter toutes les fonctions. Les options `strict` de pytest empêchent une
faute de frappe de désactiver un contrôle, et `filterwarnings` rend tout avertissement bloquant.

Créez ensuite `backend/tests/test_toolchain.py`. Ce premier test vérifie que l'interpréteur est
bien celui qui a été épinglé. Remarquez les étiquettes, obligatoires avant toute fonction :

```python
import platform
from pathlib import Path

PYTHON_VERSION_FILE = Path(__file__).resolve().parents[1] / ".python-version"


# requires : le fichier `backend/.python-version` existe et contient une version exacte.
# assigns  : rien (lecture seule du fichier).
# ensures  : réussit seulement si l'interpréteur utilisé est exactement la version épinglée.
def test_interpreter_matches_pinned_version() -> None:
    pinned_version = PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()

    assert platform.python_version() == pinned_version
```

Depuis `backend/`, les quatre commandes suivantes doivent réussir :

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy .
uv run --locked pytest
```

## Étape 5 — Installer les hooks pre-commit et gitleaks

Un hook est un script que Git lance avant de créer un commit. S'il échoue, le commit est refusé.

Créez `.pre-commit-config.yaml` **à la racine**. Pour ruff et mypy, déclarez trois hooks locaux,
sur ce modèle :

```yaml
repos:
  - repo: local
    hooks:
      - id: mypy
        name: mypy (types, mode strict)
        entry: uv run --directory backend --locked mypy .
        language: system
        files: ^backend/
        pass_filenames: false
```

Répétez ce bloc avec `ruff check .` (id `ruff-check`) et `ruff format --check .` (id `ruff-format`).
Passer par uv garantit que les hooks utilisent les versions de `uv.lock`. Puis ajoutez gitleaks :

```yaml
  - repo: https://github.com/gitleaks/gitleaks
    rev: 83d9cd684c87d95d656c1458ef04895a7f1cbd8e # frozen: v8.30.1
    hooks:
      - id: gitleaks
```

> **Sécurité.** `rev` est une empreinte de commit et non une étiquette comme `v8.30.1` : une
> étiquette peut être déplacée vers un code malveillant, pas une empreinte.

Depuis `backend/`, activez les hooks avec `uv run --locked pre-commit install`. Au premier passage,
pre-commit télécharge ce qu'il faut pour compiler gitleaks : c'est plus long.

## Étape 6 — Prouver que les hooks bloquent

Un contrôle jamais vu en échec ne prouve rien. Depuis la racine, indexez un faux secret, généré au
hasard car gitleaks ignore les exemples évidents, puis lancez le hook sans créer de commit :

```bash
TOKEN="ghp_$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 36)"
printf 'token = "%s"\n' "$TOKEN" > notes.txt
git add notes.txt
.git/hooks/pre-commit
```

Le hook doit afficher `leaks found: 1`, avec le secret masqué, et échouer. Nettoyez ensuite avec
`git rm --cached notes.txt && rm notes.txt`.

Recommencez avec `backend/bad_types.py` contenant `VALUE: int = "texte"` : mypy doit échouer.

## À retenir

- Le hook local peut être contourné avec `git commit --no-verify` : ne le faites jamais. La phase 3
  ajoutera les mêmes contrôles en CI, où ce contournement est impossible.
- Si un secret réel entre malgré tout dans l'historique, renouvelez-le. Le supprimer ne suffit pas.

**Normes concernées :** OWASP API Security Top 10 2023, API8 (mauvaise configuration) ; OWASP ASVS,
gestion des secrets et sécurité de la chaîne d'approvisionnement.
