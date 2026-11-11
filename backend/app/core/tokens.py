"""Jetons : accès (JWT signé, 15 minutes) et jetons opaques (renouvellement, liens d'e-mail)."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

# Fixé dans le code, et non dans la configuration : une erreur de réglage ne doit pas pouvoir
# allonger la durée de vie d'un jeton qui ne peut pas être retiré avant son expiration.
ACCESS_TOKEN_TTL = timedelta(minutes=15)

# Liste fixe, jamais lue depuis l'en-tête du jeton : sinon un attaquant choisirait `none`, ou un
# algorithme asymétrique dont la « clé publique » serait notre secret (confusion d'algorithme).
_ALGORITHMS = ["HS256"]
JWT_ISSUER = "taskhandler"
JWT_AUDIENCE = "taskhandler-api"
_REQUIRED_CLAIMS = ["iss", "aud", "sub", "iat", "exp", "jti", "sid", "ver"]

# 32 octets d'aléa : `secrets.token_urlsafe(32)` produit 43 caractères URL-safe.
_OPAQUE_TOKEN_BYTES = 32
OPAQUE_TOKEN_LENGTH = 43


class InvalidAccessTokenError(Exception):
    """Jeton absent, mal formé, expiré, falsifié ou incomplet : la raison n'est jamais exposée."""


class AccessTokenClaims(BaseModel):
    """Contenu vérifié d'un jeton d'accès. Aucune donnée personnelle : `sub` n'est qu'un UUID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    iss: str
    aud: str
    sub: uuid.UUID
    iat: int
    exp: int
    jti: str
    # Identifiant de la session : chaque requête vérifie qu'elle n'a pas été coupée entre-temps.
    sid: uuid.UUID
    # Copie de `users.token_version` : `logout-all` l'incrémente et invalide tous les jetons.
    ver: int = Field(ge=0)


# requires : `secret` a passé la validation de la configuration, `now` porte un fuseau horaire.
# assigns  : rien.
# ensures  : renvoie un JWT HS256 valable `ACCESS_TOKEN_TTL`, avec un identifiant unique `jti`.
def create_access_token(
    *,
    secret: SecretStr,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    token_version: int,
    now: datetime,
) -> str:
    claims = {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": str(user_id),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "jti": uuid.uuid4().hex,
        "sid": str(session_id),
        "ver": token_version,
    }
    return jwt.encode(claims, secret.get_secret_value(), algorithm=_ALGORITHMS[0])


# requires : `token` est la valeur brute reçue dans l'en-tête `Authorization`.
# assigns  : rien.
# ensures  : renvoie les revendications si la signature, l'algorithme, l'émetteur, l'audience,
#            l'expiration et la présence de chaque revendication sont valides ; sinon lève
#            `InvalidAccessTokenError`, sans préciser laquelle a échoué.
def decode_access_token(token: str, *, secret: SecretStr) -> AccessTokenClaims:
    try:
        payload = jwt.decode(
            token,
            secret.get_secret_value(),
            algorithms=_ALGORITHMS,
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            # Aucune vérification n'est désactivée : `require` ajoute seulement l'obligation de
            # présence, pour qu'un jeton sans `exp` ne soit pas considéré comme éternel.
            options={"require": _REQUIRED_CLAIMS},
        )
        return AccessTokenClaims.model_validate(payload)
    except jwt.InvalidTokenError, ValidationError:
        raise InvalidAccessTokenError from None


# requires : rien.
# assigns  : rien (lit seulement le générateur aléatoire du système).
# ensures  : renvoie un jeton opaque de 256 bits, issu de `secrets` et jamais de `random`. Sert
#            au renouvellement de session comme aux liens de vérification et de réinitialisation.
def new_opaque_token() -> str:
    return secrets.token_urlsafe(_OPAQUE_TOKEN_BYTES)


# requires : `token` est un jeton opaque.
# assigns  : rien (fonction pure).
# ensures  : renvoie son empreinte SHA-256 en hexadécimal, seule forme stockée en base.
def hash_opaque_token(token: str) -> str:
    # SHA-256 suffit ici, contrairement aux mots de passe : avec 256 bits d'aléa, un jeton ne se
    # devine pas, et une base volée ne permet pas de retrouver les jetons à partir des empreintes.
    return hashlib.sha256(token.encode("ascii")).hexdigest()
