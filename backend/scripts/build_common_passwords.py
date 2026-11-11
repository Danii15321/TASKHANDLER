"""Construit la liste des mots de passe courants à partir d'une source vérifiée.

Usage : uv run --locked python scripts/build_common_passwords.py <fichier_source>

Le fichier source se télécharge depuis SecLists, au commit indiqué dans `SOURCE_URL`. Son empreinte
est vérifiée avant tout traitement : une source modifiée, même d'un seul octet, est refusée.
"""

import hashlib
import sys
import unicodedata
from pathlib import Path

SOURCE_URL = (
    "https://raw.githubusercontent.com/danielmiessler/SecLists/"
    "1d3762046c8f9e84e1dc615e22b89adefb46c911/Passwords/Common-Credentials/"
    "xato-net-10-million-passwords-1000000.txt"
)
SOURCE_SHA256 = "424a3e03a17df0a2bc2b3ca749d81b04e79d59cb7aeec8876a5a3f308d0caf51"
OUTPUT = Path(__file__).resolve().parents[1] / "app/modules/auth/common_passwords.txt"

# Les mêmes bornes que la politique de mot de passe : un mot de passe plus court est déjà refusé
# par sa longueur, inutile de l'inclure.
MIN_LENGTH = 12
MAX_LENGTH = 128

HEADER = f"""\
# Mots de passe courants de {MIN_LENGTH} à {MAX_LENGTH} caractères, après normalisation NFKC et
# passage en minuscules. Fichier généré par scripts/build_common_passwords.py : ne pas modifier.
# Source : {SOURCE_URL}
# SHA-256 de la source : {SOURCE_SHA256}
# Licence de la source : MIT, voir common_passwords.LICENSE.
"""


# requires : `word` est une ligne de la source, sans son retour à la ligne.
# assigns  : rien (fonction pure).
# ensures  : renvoie la forme comparée par la politique : NFKC, puis minuscules.
def normalize(word: str) -> str:
    return unicodedata.normalize("NFKC", word).lower()


# requires : `source` est le fichier téléchargé depuis `SOURCE_URL`.
# assigns  : écrit `OUTPUT`.
# ensures  : écrit la liste triée et dédoublonnée ; quitte en erreur si l'empreinte diffère.
def main(source: Path) -> None:
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != SOURCE_SHA256:
        sys.exit("Empreinte inattendue : la source n'est pas celle qui a été relue.")

    words = {normalize(line) for line in content.decode("utf-8", "replace").splitlines()}
    # Une entrée commençant par `#` serait prise pour un commentaire au chargement.
    kept = sorted(
        word
        for word in words
        if MIN_LENGTH <= len(word) <= MAX_LENGTH and not word.startswith("#") and "�" not in word
    )
    OUTPUT.write_text(HEADER + "\n".join(kept) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage : build_common_passwords.py <fichier_source>")
    main(Path(sys.argv[1]))
