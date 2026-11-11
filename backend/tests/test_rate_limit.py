"""Tests du limiteur sur un vrai Redis : fenêtre glissante, temporisation, droits et pannes."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from pydantic import SecretStr, ValidationError
from redis.asyncio import Redis
from redis.exceptions import NoPermissionError

from app.core.config import Settings
from app.core.rate_limit import (
    BACKOFF_CAP_MS,
    BACKOFF_FREE_FAILURES,
    Limit,
    RateLimiter,
    RateLimitExceededError,
    RateLimitUnavailableError,
    _bounded_int,
    create_redis_client,
)
from tests.conftest import (
    UNUSED_CREDENTIAL,
    DatabaseTestSettings,
    unique_rate_limit_prefix,
    unreachable_database_url,
    unreachable_redis_url,
)

pytestmark = pytest.mark.anyio

SCOPE = "test-backoff"


# requires : `database_settings` fournit l'URL du Redis de test.
# assigns  : ouvre puis ferme une connexion Redis.
# ensures  : fournit un client Redis réel, fermé à la fin du test.
@pytest.fixture
async def redis_client(database_settings: DatabaseTestSettings) -> AsyncIterator[Redis]:
    settings = Settings(
        environment="test",
        database_url=unreachable_database_url(),
        redis_url=database_settings.redis_url,
        _env_file=None,
    )
    client = create_redis_client(settings)
    yield client
    await client.aclose()


# requires : `redis_client` est relié au Redis de test.
# assigns  : rien.
# ensures  : fournit un limiteur dont les clés ne croisent celles d'aucun autre test.
@pytest.fixture
def limiter(redis_client: Redis) -> RateLimiter:
    return RateLimiter(redis_client, key_prefix=unique_rate_limit_prefix())


# requires : `limiter` est relié au Redis de test.
# assigns  : enregistre des requêtes dans une fenêtre propre au test.
# ensures  : la limite laisse passer exactement `max_requests` requêtes, puis refuse avec un délai
#            positif et inférieur à la fenêtre.
async def test_sliding_window_blocks_after_limit(limiter: RateLimiter) -> None:
    limit = Limit("test", max_requests=3, window_seconds=60)
    for _ in range(3):
        await limiter.hit(limit, "192.0.2.1")

    with pytest.raises(RateLimitExceededError) as error:
        await limiter.hit(limit, "192.0.2.1")

    assert 1 <= error.value.retry_after_seconds <= 60
    await limiter.hit(limit, "192.0.2.2")


# requires : `limiter` est relié au Redis de test.
# assigns  : enregistre des requêtes dans une fenêtre d'une seconde.
# ensures  : la limite se libère d'elle-même : il n'y a jamais de blocage définitif.
async def test_sliding_window_releases_after_window(limiter: RateLimiter) -> None:
    limit = Limit("test", max_requests=1, window_seconds=1)
    await limiter.hit(limit, "192.0.2.1")
    with pytest.raises(RateLimitExceededError):
        await limiter.hit(limit, "192.0.2.1")

    await asyncio.sleep(1.1)

    await limiter.hit(limit, "192.0.2.1")


# requires : `limiter` est relié au Redis de test.
# assigns  : envoie 20 requêtes simultanées dans une même fenêtre.
# ensures  : le compteur est atomique : jamais plus de requêtes acceptées que la limite.
async def test_sliding_window_is_atomic_under_concurrency(limiter: RateLimiter) -> None:
    limit = Limit("test", max_requests=5, window_seconds=60)

    results = await asyncio.gather(
        *(limiter.hit(limit, "192.0.2.1") for _ in range(20)), return_exceptions=True
    )

    assert sum(result is None for result in results) == 5
    assert all(isinstance(r, RateLimitExceededError) for r in results if r is not None)


# requires : `limiter` est relié au Redis de test.
# assigns  : enregistre des échecs pour une identité propre au test.
# ensures  : les premiers échecs sont gratuits, puis le délai double à chaque échec.
async def test_backoff_grows_progressively(limiter: RateLimiter) -> None:
    for _ in range(BACKOFF_FREE_FAILURES - 1):
        await limiter.record_failure(SCOPE, "victime")
    await limiter.ensure_backoff_elapsed(SCOPE, "victime")

    delays = []
    for _ in range(3):
        await limiter.record_failure(SCOPE, "victime")
        with pytest.raises(RateLimitExceededError) as error:
            await limiter.ensure_backoff_elapsed(SCOPE, "victime")
        delays.append(error.value.retry_after_seconds)

    assert delays == [1, 2, 4]


# requires : `limiter` est relié au Redis de test.
# assigns  : enregistre un très grand nombre d'échecs.
# ensures  : le délai plafonne à 15 minutes, quel que soit le nombre d'échecs : jamais de
#            verrouillage définitif.
async def test_backoff_is_capped(limiter: RateLimiter) -> None:
    for _ in range(80):
        await limiter.record_failure(SCOPE, "victime")

    with pytest.raises(RateLimitExceededError) as error:
        await limiter.ensure_backoff_elapsed(SCOPE, "victime")

    assert error.value.retry_after_seconds == BACKOFF_CAP_MS // 1000


# requires : `limiter` est relié au Redis de test.
# assigns  : enregistre des échecs, puis les efface.
# ensures  : une réussite remet la temporisation à zéro.
async def test_success_resets_backoff(limiter: RateLimiter) -> None:
    for _ in range(BACKOFF_FREE_FAILURES + 2):
        await limiter.record_failure(SCOPE, "victime")

    await limiter.reset_failures(SCOPE, "victime")

    await limiter.ensure_backoff_elapsed(SCOPE, "victime")


# requires : rien.
# assigns  : rien.
# ensures  : les clés ne contiennent ni l'IP ni l'adresse en clair, et restent de taille fixe.
async def test_keys_contain_no_personal_data(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, key_prefix="rl:")
    identity = "203.0.113.7|alice@exemple.org"

    key = limiter.key_for("login-account", identity)

    assert "alice" not in key
    assert "203.0.113.7" not in key
    assert len(limiter.key_for("x", identity * 1000)) == len(limiter.key_for("x", "a"))


# requires : `command` envoie une commande hors des droits de l'utilisateur applicatif.
# assigns  : tente la commande, refusée par Redis.
# ensures  : l'utilisateur de l'application ne peut ni vider Redis, ni lire sa configuration, ni
#            lister les clés ou les utilisateurs, ni écrire hors de `rl:*` ou hors des commandes
#            du limiteur.
@pytest.mark.parametrize(
    "command",
    [
        lambda client: client.flushall(),
        lambda client: client.config_get("*"),
        lambda client: client.keys("*"),
        lambda client: client.hset("autre:cle", "champ", "1"),
        lambda client: client.set("rl:x", "1"),
        lambda client: client.acl_list(),
    ],
    ids=["flushall", "config", "keys", "hors-prefixe", "set", "acl"],
)
async def test_application_user_is_restricted(
    redis_client: Redis, command: Callable[[Redis], Awaitable[object]]
) -> None:
    with pytest.raises(NoPermissionError):
        await command(redis_client)


# requires : rien.
# assigns  : tente une connexion vers un port fermé.
# ensures  : Redis injoignable donne une erreur dédiée, vite, sans blocage.
async def test_unreachable_redis_fails_fast() -> None:
    settings = Settings(
        environment="test",
        database_url=unreachable_database_url(),
        redis_url=SecretStr(f"redis://taskhandler_app:{UNUSED_CREDENTIAL}@127.0.0.1:1/0"),
        _env_file=None,
    )
    client = create_redis_client(settings)
    limiter = RateLimiter(client, key_prefix="rl:")
    started = time.perf_counter()
    try:
        with pytest.raises(RateLimitUnavailableError):
            await limiter.hit(Limit("test", 1, 60), "192.0.2.1")
    finally:
        await client.aclose()

    assert time.perf_counter() - started < 2


# requires : `value` est une réponse incohérente pour le limiteur.
# assigns  : rien.
# ensures  : une réponse altérée de Redis est refusée, jamais interprétée (OWASP API10).
@pytest.mark.parametrize("value", [None, "1", b"1", -1, 10**12, True, [1]])
def test_untrusted_redis_answers_are_rejected(value: object) -> None:
    with pytest.raises(RateLimitUnavailableError):
        _bounded_int(value, 60_000)


# requires : `url` est une URL Redis à refuser.
# assigns  : rien.
# ensures  : l'URL est refusée, sans que son mot de passe soit recopié.
@pytest.mark.parametrize(
    "url",
    [
        f"http://taskhandler_app:{UNUSED_CREDENTIAL}@127.0.0.1:6379/0",
        f"redis://default:{UNUSED_CREDENTIAL}@127.0.0.1:6379/0",
        "redis://taskhandler_app:court@127.0.0.1:6379/0",
        "redis://taskhandler_app@127.0.0.1:6379/0",
        f"redis://taskhandler_app:{UNUSED_CREDENTIAL}@127.0.0.1/0",
        f"redis://taskhandler_app:{UNUSED_CREDENTIAL}@127.0.0.1:99999/0",
    ],
)
def test_refuses_unsafe_redis_url(url: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(
            environment="test",
            database_url=unreachable_database_url(),
            redis_url=SecretStr(url),
            _env_file=None,
        )

    assert UNUSED_CREDENTIAL not in str(error.value)


# requires : rien.
# assigns  : rien.
# ensures  : la production exige une connexion chiffrée vers Redis.
def test_production_requires_tls_for_redis() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url=unreachable_database_url("?ssl=verify-full"),
            redis_url=SecretStr(f"redis://taskhandler_app:{UNUSED_CREDENTIAL}@127.0.0.1:6379/0"),
            _env_file=None,
        )

    Settings(
        database_url=unreachable_database_url("?ssl=verify-full"),
        redis_url=unreachable_redis_url(),
        _env_file=None,
    )
