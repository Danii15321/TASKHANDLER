"""Schémas d'entrée de l'authentification."""

from typing import Annotated, Self

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from app.modules.auth.password_policy import (
    MAX_PASSWORD_LENGTH,
    ensure_password_unrelated_to_email,
    validate_password,
)

# Longueur maximale avant validation : une adresse plus longue est forcément invalide (RFC 5321
# limite le chemin complet à 254 caractères), inutile de faire travailler le validateur.
_MAX_RAW_EMAIL_LENGTH = 320


# requires : `value` est une chaîne d'au plus `_MAX_RAW_EMAIL_LENGTH` caractères.
# assigns  : rien (aucune requête DNS : la vérification de délivrabilité est désactivée).
# ensures  : renvoie l'adresse normalisée et en minuscules ; sinon lève `ValueError`, avec un
#            message qui ne recopie pas l'adresse reçue.
def normalize_email(value: str) -> str:
    try:
        result = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError:
        raise ValueError("adresse e-mail invalide") from None
    # Les messageries traitent la casse comme indifférente : `Alice@` et `alice@` sont un seul
    # compte. Les points et les `+` ne sont pas retirés, car ce n'est vrai que chez certains.
    return result.normalized.lower()


NormalizedEmail = Annotated[
    str, Field(max_length=_MAX_RAW_EMAIL_LENGTH), AfterValidator(normalize_email)
]
# `repr=False` : le mot de passe n'apparaît pas si le modèle est affiché ou journalisé.
NewPassword = Annotated[
    str, Field(max_length=MAX_PASSWORD_LENGTH, repr=False), AfterValidator(validate_password)
]


class RegisterRequest(BaseModel):
    """Corps de `POST /auth/register` : uniquement l'adresse et le mot de passe.

    `extra="forbid"` refuse tout autre champ (`id`, `is_active`, `password_hash`…) : un client ne
    peut pas fixer lui-même une propriété du compte (OWASP API3, affectation de masse).
    """

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    email: NormalizedEmail
    password: NewPassword

    # requires : `email` et `password` ont passé leurs validations individuelles.
    # assigns  : rien.
    # ensures  : renvoie la demande si le mot de passe ne reprend pas l'adresse ; sinon lève
    #            `ValueError`.
    @model_validator(mode="after")
    def _password_unrelated_to_email(self) -> Self:
        ensure_password_unrelated_to_email(self.password, self.email)
        return self
