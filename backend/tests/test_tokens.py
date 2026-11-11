"""Tests des jetons : signature, algorithme, revendications et jetons de renouvellement."""

import base64
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.tokens import (
    ACCESS_TOKEN_TTL,
    JWT_AUDIENCE,
    JWT_ISSUER,
    REFRESH_TOKEN_LENGTH,
    InvalidAccessTokenError,
    create_access_token,
    decode_access_token,
    hash_refresh_token,
    new_refresh_token,
)
from tests.conftest import unreachable_database_url

SECRET = SecretStr(secrets.token_hex(32))
USER_ID = uuid.uuid4()
SESSION_ID = uuid.uuid4()
EXPECTED_CLAIMS = {"iss", "aud", "sub", "iat", "exp", "jti", "sid", "ver"}


# requires : `now` porte un fuseau horaire.
# assigns  : rien.
# ensures  : renvoie un jeton d'accès émis par le code de production.
def issue(now: datetime | None = None, secret: SecretStr = SECRET) -> str:
    return create_access_token(
        secret=secret,
        user_id=USER_ID,
        session_id=SESSION_ID,
        token_version=0,
        now=now if now is not None else datetime.now(UTC),
    )


# requires : rien.
# assigns  : rien.
# ensures  : renvoie des revendications complètes et valides, à modifier par chaque test.
def valid_claims() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "sub": str(USER_ID),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "jti": uuid.uuid4().hex,
        "sid": str(SESSION_ID),
        "ver": 0,
    }


# requires : rien.
# assigns  : rien.
# ensures  : renvoie un segment JWT encodé en base64url, sans remplissage.
def segment(data: dict[str, object]) -> str:
    raw = json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# requires : rien.
# assigns  : rien.
# ensures  : un jeton émis par le code de production est accepté et décodé fidèlement.
def test_issued_token_round_trips() -> None:
    claims = decode_access_token(issue(), secret=SECRET)

    assert claims.sub == USER_ID
    assert claims.sid == SESSION_ID
    assert claims.ver == 0
    assert claims.exp - claims.iat == int(ACCESS_TOKEN_TTL.total_seconds())


# requires : rien.
# assigns  : rien.
# ensures  : le jeton ne contient que les revendications prévues, et aucune donnée personnelle.
def test_token_contains_no_personal_data() -> None:
    payload = jwt.decode(issue(), options={"verify_signature": False})

    assert set(payload) == EXPECTED_CLAIMS
    assert "@" not in json.dumps(payload)


# requires : rien.
# assigns  : rien.
# ensures  : deux jetons émis au même instant ont des identifiants `jti` différents.
def test_each_token_has_unique_identifier() -> None:
    now = datetime.now(UTC)

    assert (
        decode_access_token(issue(now), secret=SECRET).jti
        != decode_access_token(issue(now), secret=SECRET).jti
    )


# requires : rien.
# assigns  : rien.
# ensures  : un jeton expiré est refusé.
def test_expired_token_is_refused() -> None:
    expired = issue(datetime.now(UTC) - ACCESS_TOKEN_TTL - timedelta(seconds=1))

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(expired, secret=SECRET)


# requires : rien.
# assigns  : rien.
# ensures  : un jeton signé avec une autre clé est refusé.
def test_token_signed_with_another_key_is_refused() -> None:
    forged = issue(secret=SecretStr(secrets.token_hex(32)))

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(forged, secret=SECRET)


# requires : rien.
# assigns  : rien.
# ensures  : un jeton sans signature (`alg=none`) est refusé, même avec des revendications valides.
def test_unsigned_token_is_refused() -> None:
    claims = {**valid_claims(), "iat": 0, "exp": 2**40}
    unsigned = f"{segment({'alg': 'none', 'typ': 'JWT'})}.{segment(claims)}."

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(unsigned, secret=SECRET)


# requires : rien.
# assigns  : rien.
# ensures  : un autre algorithme est refusé même avec la bonne clé : la liste est fixe.
def test_other_algorithm_is_refused() -> None:
    other = jwt.encode(valid_claims(), SECRET.get_secret_value(), algorithm="HS512")

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(other, secret=SECRET)


# requires : `claim` est une revendication obligatoire.
# assigns  : rien.
# ensures  : un jeton correctement signé mais incomplet est refusé.
@pytest.mark.parametrize("claim", sorted(EXPECTED_CLAIMS))
def test_token_missing_a_claim_is_refused(claim: str) -> None:
    claims = valid_claims()
    del claims[claim]
    incomplete = jwt.encode(claims, SECRET.get_secret_value(), algorithm="HS256")

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(incomplete, secret=SECRET)


# requires : `claim` et `value` décrivent une revendication invalide.
# assigns  : rien.
# ensures  : un jeton signé mais destiné à un autre service, ou mal formé, est refusé.
@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("iss", "un-autre-emetteur"),
        ("aud", "un-autre-service"),
        ("sub", "pas-un-uuid"),
        ("sid", "pas-un-uuid"),
        ("ver", -1),
    ],
)
def test_token_with_invalid_claim_is_refused(claim: str, value: object) -> None:
    invalid = jwt.encode(
        {**valid_claims(), claim: value}, SECRET.get_secret_value(), algorithm="HS256"
    )

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(invalid, secret=SECRET)


# requires : rien.
# assigns  : rien.
# ensures  : un jeton dont le contenu a été modifié après signature est refusé.
def test_tampered_token_is_refused() -> None:
    header, _payload, signature = issue().split(".")
    tampered_payload = segment({**valid_claims(), "iat": 0, "exp": 2**40, "ver": 99})

    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(f"{header}.{tampered_payload}.{signature}", secret=SECRET)


# requires : `token` n'est pas un JWT.
# assigns  : rien.
# ensures  : une valeur quelconque est refusée proprement, sans autre exception.
@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "Bearer x", "." * 3])
def test_garbage_is_refused(token: str) -> None:
    with pytest.raises(InvalidAccessTokenError):
        decode_access_token(token, secret=SECRET)


# requires : rien.
# assigns  : rien.
# ensures  : chaque jeton de renouvellement est unique, au format attendu, et stocké haché.
def test_refresh_tokens_are_random_and_hashed() -> None:
    tokens = {new_refresh_token() for _ in range(100)}

    assert len(tokens) == 100
    for token in tokens:
        assert len(token) == REFRESH_TOKEN_LENGTH
        token_hash = hash_refresh_token(token)
        assert len(token_hash) == 64
        assert token not in token_hash
        assert hash_refresh_token(token) == token_hash


# requires : `secret` enfreint la règle de la clé de signature.
# assigns  : remplace la clé JWT de l'environnement le temps du test.
# ensures  : une clé trop courte, répétitive ou d'exemple empêche le démarrage, sans être recopiée.
@pytest.mark.parametrize(
    "secret",
    [
        secrets.token_hex(16),
        "ab" * 40,
        "remplacer" + secrets.token_hex(32),
        "change" + secrets.token_hex(32),
    ],
)
def test_refuses_weak_jwt_secret(monkeypatch: pytest.MonkeyPatch, secret: str) -> None:
    monkeypatch.setenv("TASKHANDLER_JWT_SECRET", secret)

    with pytest.raises(ValidationError) as error:
        Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)

    assert secret not in str(error.value)


# requires : rien.
# assigns  : retire la clé JWT de l'environnement le temps du test.
# ensures  : sans clé de signature, l'application refuse de démarrer.
def test_refuses_to_start_without_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TASKHANDLER_JWT_SECRET")

    with pytest.raises(ValidationError):
        Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)


# requires : rien.
# assigns  : fixe des durées de session incohérentes le temps du test.
# ensures  : une inactivité permise plus longue que la durée maximale est refusée.
def test_refuses_idle_time_longer_than_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASKHANDLER_REFRESH_TOKEN_IDLE_DAYS", "20")
    monkeypatch.setenv("TASKHANDLER_SESSION_MAX_DAYS", "10")

    with pytest.raises(ValidationError):
        Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)
