"""Limitation de débit : fenêtre glissante et temporisation progressive, atomiques dans Redis.

Chaque opération est un script Lua : Redis l'exécute d'un bloc, sans qu'aucune autre requête ne
s'intercale. Deux requêtes simultanées ne peuvent donc pas lire le même compteur puis passer
toutes les deux.
"""

import hashlib
import logging
import math
import ssl
import uuid
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings

audit_logger = logging.getLogger("app.audit")

# Redis est local et rapide : au-delà d'une demi-seconde, il est considéré comme indisponible,
# plutôt que de faire attendre chaque requête d'authentification.
_REDIS_TIMEOUT_SECONDS = 0.5

# Temporisation progressive : 5 échecs gratuits, puis 1 s, 2 s, 4 s… jamais plus de 15 minutes
# entre deux essais. Il n'y a jamais de verrouillage définitif, qui permettrait à un attaquant de
# bloquer un compte à distance. Le compteur s'efface après 24 heures sans échec.
BACKOFF_FREE_FAILURES = 5
BACKOFF_BASE_MS = 1_000
BACKOFF_CAP_MS = 15 * 60 * 1_000
_BACKOFF_MEMORY_MS = 24 * 60 * 60 * 1_000

# L'heure vient de Redis (`TIME`), pas du serveur d'application : plusieurs instances de l'API
# partagent ainsi la même horloge, même si les leurs dérivent.
_NOW_MS = (
    "local clock = redis.call('TIME')\n"
    "local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)\n"
)

# Fenêtre glissante : une entrée par requête acceptée, datée en millisecondes. Une requête
# refusée n'est pas comptée : la limite se libère d'elle-même à la fin de la fenêtre.
_SLIDING_WINDOW_SCRIPT = (
    _NOW_MS
    + """
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
if redis.call('ZCARD', KEYS[1]) < limit then
  redis.call('ZADD', KEYS[1], now, ARGV[3])
  redis.call('PEXPIRE', KEYS[1], window)
  return {1, 0}
end
local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return {0, tonumber(oldest[2]) + window - now}
"""
)

_BACKOFF_CHECK_SCRIPT = (
    _NOW_MS
    + """
local failures = tonumber(redis.call('HGET', KEYS[1], 'failures') or '0')
local free = tonumber(ARGV[1])
if failures < free then
  return 0
end
local last = tonumber(redis.call('HGET', KEYS[1], 'last') or '0')
local wait = math.min(tonumber(ARGV[2]) * (2 ^ (failures - free)), tonumber(ARGV[3]))
local remaining = last + wait - now
if remaining > 0 then
  return math.ceil(remaining)
end
return 0
"""
)

_BACKOFF_FAILURE_SCRIPT = (
    _NOW_MS
    + """
redis.call('HINCRBY', KEYS[1], 'failures', 1)
redis.call('HSET', KEYS[1], 'last', now)
redis.call('PEXPIRE', KEYS[1], ARGV[1])
return 1
"""
)


class RateLimitUnavailableError(Exception):
    """Redis est injoignable, trop lent, ou a renvoyé une réponse incohérente."""


class RateLimitExceededError(Exception):
    """La limite est atteinte ; `retry_after_seconds` indique quand réessayer."""

    # requires : `retry_after_seconds` ≥ 1.
    # assigns  : rien.
    # ensures  : l'exception porte le nom de la limite et le délai à respecter.
    def __init__(self, limit_name: str, retry_after_seconds: int) -> None:
        super().__init__(limit_name)
        self.limit_name = limit_name
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class Limit:
    """Au plus `max_requests` requêtes par fenêtre glissante de `window_seconds` secondes."""

    name: str
    max_requests: int
    window_seconds: int


# requires : `milliseconds` ≥ 0.
# assigns  : rien.
# ensures  : renvoie le délai en secondes entières, arrondi vers le haut et au moins 1.
def _to_retry_after(milliseconds: int) -> int:
    return max(1, math.ceil(milliseconds / 1000))


# requires : `value` est une réponse brute de Redis.
# assigns  : rien.
# ensures  : renvoie l'entier si `value` en est un dans `[0, maximum]` ; sinon lève
#            `RateLimitUnavailableError`. Une réponse de Redis est une donnée externe : elle est
#            vérifiée avant de décider quoi que ce soit (OWASP API10).
def _bounded_int(value: object, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise RateLimitUnavailableError
    return value


class RateLimiter:
    """Limiteur partagé par toutes les instances de l'API, grâce à Redis."""

    # requires : `key_prefix` respecte le motif `rl:…` autorisé par les droits ACL de Redis.
    # assigns  : enregistre les scripts Lua auprès du client (sans requête réseau).
    # ensures  : le limiteur est prêt ; aucune connexion n'est ouverte avant le premier appel.
    def __init__(self, redis: Redis, *, key_prefix: str) -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._sliding_window = redis.register_script(_SLIDING_WINDOW_SCRIPT)
        self._backoff_check = redis.register_script(_BACKOFF_CHECK_SCRIPT)
        self._backoff_failure = redis.register_script(_BACKOFF_FAILURE_SCRIPT)

    # requires : `identity` identifie ce qui est limité (IP, adresse, couple, utilisateur).
    # assigns  : rien (fonction pure).
    # ensures  : renvoie la clé Redis. L'identité y figure hachée : ni IP ni adresse e-mail en
    #            clair dans Redis, et une identité géante ne donne pas une clé géante.
    def key_for(self, scope: str, identity: str) -> str:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}{scope}:{digest}"

    # requires : `limit.max_requests` ≥ 1 et `limit.window_seconds` ≥ 1.
    # assigns  : enregistre la requête dans la fenêtre si elle est acceptée.
    # ensures  : se termine si la requête est sous la limite ; sinon lève `RateLimitExceededError` ;
    #            lève `RateLimitUnavailableError` si Redis ne répond pas correctement.
    async def hit(self, limit: Limit, identity: str) -> None:
        window_ms = limit.window_seconds * 1000
        try:
            result = await self._sliding_window(
                keys=[self.key_for(limit.name, identity)],
                args=[window_ms, limit.max_requests, uuid.uuid4().hex],
            )
        except RedisError, OSError, TimeoutError:
            raise RateLimitUnavailableError from None
        if not isinstance(result, list) or len(result) != 2:
            raise RateLimitUnavailableError
        allowed = _bounded_int(result[0], 1)
        wait_ms = _bounded_int(result[1], window_ms)
        if not allowed:
            self._exceeded(limit.name, _to_retry_after(wait_ms))

    # requires : rien.
    # assigns  : rien (lecture seule).
    # ensures  : se termine si la temporisation est écoulée ; sinon lève `RateLimitExceededError`.
    async def ensure_backoff_elapsed(self, scope: str, identity: str) -> None:
        try:
            result = await self._backoff_check(
                keys=[self.key_for(scope, identity)],
                args=[BACKOFF_FREE_FAILURES, BACKOFF_BASE_MS, BACKOFF_CAP_MS],
            )
        except RedisError, OSError, TimeoutError:
            raise RateLimitUnavailableError from None
        wait_ms = _bounded_int(result, BACKOFF_CAP_MS)
        if wait_ms > 0:
            self._exceeded(scope, _to_retry_after(wait_ms))

    # requires : un essai vient d'échouer pour `identity`.
    # assigns  : incrémente le nombre d'échecs et date le dernier, pour 24 heures.
    # ensures  : la prochaine temporisation tient compte de cet échec.
    async def record_failure(self, scope: str, identity: str) -> None:
        try:
            await self._backoff_failure(
                keys=[self.key_for(scope, identity)], args=[_BACKOFF_MEMORY_MS]
            )
        except RedisError, OSError, TimeoutError:
            raise RateLimitUnavailableError from None

    # requires : un essai vient de réussir pour `identity`.
    # assigns  : efface le compteur d'échecs.
    # ensures  : les échecs passés ne pénalisent plus les prochains essais.
    async def reset_failures(self, scope: str, identity: str) -> None:
        try:
            await self._redis.delete(self.key_for(scope, identity))
        except RedisError, OSError, TimeoutError:
            raise RateLimitUnavailableError from None

    # requires : rien.
    # assigns  : rien.
    # ensures  : se termine si Redis répond ; sinon lève `RateLimitUnavailableError`.
    async def ping(self) -> None:
        try:
            await self._redis.ping()
        except RedisError, OSError, TimeoutError:
            raise RateLimitUnavailableError from None

    # requires : `retry_after_seconds` ≥ 1.
    # assigns  : écrit un événement d'audit.
    # ensures  : lève toujours `RateLimitExceededError`.
    def _exceeded(self, limit_name: str, retry_after_seconds: int) -> None:
        # Seul le nom de la limite est journalisé : ni IP, ni adresse, ni identifiant haché.
        audit_logger.warning("rate_limited", extra={"limit": limit_name})
        raise RateLimitExceededError(limit_name, retry_after_seconds)


# requires : `settings` est valide.
# assigns  : rien (aucune connexion n'est ouverte avant la première commande).
# ensures  : renvoie un client Redis aux délais courts, qui échoue vite si Redis est en panne.
def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_timeout=_REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
        # Pas de nouvel essai automatique : un Redis lent doit donner une réponse rapide, même 503.
        retry_on_timeout=False,
        **redis_tls_options(settings),
    )


# requires : `settings` est valide.
# assigns  : rien.
# ensures  : avec `rediss://`, renvoie les options qui imposent un certificat valide pour le nom
#            d'hôte, TLS 1.2 au moins et l'autorité interne ; avec `redis://`, aucune option.
def redis_tls_options(settings: Settings) -> dict[str, Any]:
    if not settings.redis_url.get_secret_value().startswith("rediss://"):
        return {}
    # Valeurs explicites plutôt que les défauts de redis-py : un changement de défaut dans une
    # future version ne doit pas pouvoir désactiver la vérification sans bruit.
    options: dict[str, Any] = {
        "ssl_cert_reqs": "required",
        "ssl_check_hostname": True,
        "ssl_min_version": ssl.TLSVersion.TLSv1_2,
    }
    if settings.tls_ca_file is not None:
        # redis-py ajoute ce fichier aux autorités du système, comme `client_tls_context`.
        options["ssl_ca_certs"] = str(settings.tls_ca_file)
    return options


# requires : l'application a été assemblée par `create_app`.
# assigns  : rien.
# ensures  : renvoie le limiteur partagé par toute l'application.
def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


RateLimiterDependency = Annotated[RateLimiter, Depends(get_rate_limiter)]
