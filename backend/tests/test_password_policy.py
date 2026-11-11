"""Tests de la politique de mot de passe et de la liste des mots de passe courants."""

import pytest

from app.modules.auth.password_policy import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    common_passwords,
    ensure_password_unrelated_to_email,
    validate_password,
)

PASSPHRASE = "violet ruisseau lanterne 2026"


# requires : `password` respecte la politique.
# assigns  : rien.
# ensures  : le mot de passe est accepté et renvoyé normalisé.
@pytest.mark.parametrize(
    "password",
    [
        PASSPHRASE,
        "violet-train",  # 12 caractères exactement
        "é" * 6 + "ü" * 6,
        "mot de passe en français, avec espaces",
        "x" * (MAX_PASSWORD_LENGTH - 1) + "y",
    ],
)
def test_accepts_valid_passwords(password: str) -> None:
    assert validate_password(password) == password


# requires : `password` enfreint la politique.
# assigns  : rien.
# ensures  : le mot de passe est refusé, sans que le message d'erreur ne le recopie.
@pytest.mark.parametrize(
    "password",
    [
        "x" * (MIN_PASSWORD_LENGTH - 2) + "y",
        "x" * MAX_PASSWORD_LENGTH + "y",
        # 65 ligatures « fi » : 65 caractères saisis, mais 130 après normalisation.
        "\N{LATIN SMALL LIGATURE FI}" * 65,
        "violet\x00ruisseau",
        "violet\nruisseau",
        " " * 20,
        "z" * 20,
        "asdfasdfasdf",
        "ASDFASDFASDF",
    ],
)
def test_refuses_invalid_passwords(password: str) -> None:
    with pytest.raises(ValueError) as error:
        validate_password(password)

    assert password not in str(error.value)


# requires : rien.
# assigns  : rien.
# ensures  : les formes Unicode équivalentes donnent le même mot de passe (NFKC).
def test_normalizes_equivalent_unicode_forms() -> None:
    # Lettres « pleine chasse » : même dessin que l'ASCII, décalées de 0xFEE0 dans Unicode.
    fullwidth = "".join(chr(ord(letter) + 0xFEE0) for letter in "violet") + " ruisseau lanterne"

    assert validate_password(fullwidth) == "violet ruisseau lanterne"


# requires : `password` reprend l'adresse `alice.martin@exemple.org`.
# assigns  : rien.
# ensures  : un mot de passe construit à partir de l'adresse est refusé.
@pytest.mark.parametrize(
    "password", ["alice.martin@exemple.org!", "mon-alice.martin-2026", "ALICE.MARTIN 2026 ok"]
)
def test_refuses_password_containing_email(password: str) -> None:
    with pytest.raises(ValueError):
        ensure_password_unrelated_to_email(password, "alice.martin@exemple.org")


# requires : rien.
# assigns  : rien.
# ensures  : une partie d'adresse trop courte ne bloque pas un mot de passe sain.
def test_short_email_part_does_not_block_password() -> None:
    ensure_password_unrelated_to_email("jo et le violet ruisseau", "jo@exemple.org")


# requires : le fichier de la liste est présent.
# assigns  : rien.
# ensures  : la liste est complète, normalisée, et son en-tête n'est pas pris pour une entrée.
def test_common_password_list_is_complete_and_normalized() -> None:
    entries = common_passwords()

    assert len(entries) > 40_000
    assert all(MIN_PASSWORD_LENGTH <= len(entry) <= MAX_PASSWORD_LENGTH for entry in entries)
    assert all(entry == entry.lower() and not entry.startswith("#") for entry in entries)
