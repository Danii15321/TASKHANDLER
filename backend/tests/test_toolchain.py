"""Vérifie que la chaîne d'outils de développement est correctement en place."""

import platform
from pathlib import Path

# Le fichier `.python-version` se trouve à la racine de `backend/`, un niveau au-dessus de `tests/`.
PYTHON_VERSION_FILE = Path(__file__).resolve().parents[1] / ".python-version"


# requires : le fichier `backend/.python-version` existe et contient une version exacte.
# assigns  : rien (lecture seule du fichier).
# ensures  : réussit seulement si l'interpréteur utilisé est exactement la version épinglée.
def test_interpreter_matches_pinned_version() -> None:
    # Une version différente de celle épinglée signifierait que les tests ne s'exécutent
    # pas dans l'environnement prévu : leurs résultats ne seraient alors plus fiables.
    pinned_version = PYTHON_VERSION_FILE.read_text(encoding="utf-8").strip()

    assert platform.python_version() == pinned_version
