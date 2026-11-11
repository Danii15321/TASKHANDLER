"""Politique de mot de passe, conforme au NIST SP 800-63B : longueur et liste de refus.

Aucune règle de composition (majuscule, chiffre, symbole) : elles poussent vers des mots de passe
prévisibles comme `Motdepasse1!`, sans gain réel de sécurité.
"""

import unicodedata
from functools import cache
from pathlib import Path

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

# En dessous, une partie de l'adresse trop courte (`jo`, `ana`) refuserait des mots de passe sains.
_MIN_EMAIL_PART_LENGTH = 4
_COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")


# requires : le fichier `common_passwords.txt` est présent à côté de ce module.
# assigns  : met la liste en cache mémoire au premier appel.
# ensures  : renvoie l'ensemble des mots de passe courants, déjà normalisés et en minuscules.
@cache
def common_passwords() -> frozenset[str]:
    lines = _COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(line for line in lines if line and not line.startswith("#"))


# requires : rien.
# assigns  : rien (fonction pure).
# ensures  : renvoie le mot de passe en forme NFKC. L'inscription et la connexion passent toutes
#            deux par ici : sinon, un mot de passe correct pourrait être refusé.
def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


# requires : `password` contient au plus `MAX_PASSWORD_LENGTH` caractères avant normalisation.
# assigns  : rien (fonction pure, en dehors du cache de la liste).
# ensures  : renvoie le mot de passe normalisé en NFKC s'il respecte la politique ; sinon lève
#            `ValueError`, dont le message ne contient jamais le mot de passe.
def validate_password(password: str) -> str:
    # NFKC : une lettre « pleine chasse » (claviers asiatiques) et sa forme ASCII donnent le même
    # mot de passe, quel que soit le clavier utilisé.
    # La connexion devra appliquer exactement la même normalisation avant de vérifier.
    normalized = normalize_password(password)

    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise ValueError("le mot de passe contient des caractères de contrôle")
    # La longueur est comptée après normalisation, sans troncature : la normalisation peut
    # allonger le texte (`ﬁ` devient `fi`), et aucun caractère saisi n'est ignoré en silence.
    if len(normalized) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères")
    if len(normalized) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"le mot de passe doit contenir au plus {MAX_PASSWORD_LENGTH} caractères")
    if len(set(normalized)) == 1:
        raise ValueError("le mot de passe ne peut pas répéter un seul caractère")
    if normalized.lower() in common_passwords():
        raise ValueError("ce mot de passe figure dans une liste de mots de passe courants")
    return normalized


# requires : `password` est normalisé, `email` est l'adresse normalisée du même compte.
# assigns  : rien (fonction pure).
# ensures  : ne renvoie rien si le mot de passe ne reprend pas l'adresse ; sinon lève `ValueError`.
def ensure_password_unrelated_to_email(password: str, email: str) -> None:
    lowered_password = password.lower()
    local_part = email.rsplit("@", 1)[0]
    if email in lowered_password or (
        len(local_part) >= _MIN_EMAIL_PART_LENGTH and local_part in lowered_password
    ):
        # Un attaquant qui connaît l'adresse essaiera d'abord ses variantes.
        raise ValueError("le mot de passe ne doit pas reprendre l'adresse e-mail")
