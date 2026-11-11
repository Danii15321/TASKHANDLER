"""Tests du hachage argon2id : paramètres, sel, fil d'exécution séparé et nombre borné."""

import asyncio
import threading
import time

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.passwords import PasswordHasherService, PasswordHashingBusyError
from tests.conftest import unreachable_database_url

pytestmark = pytest.mark.anyio

PASSPHRASE = "violet ruisseau lanterne 2026"
REQUIRED_PREFIX = "$argon2id$v=19$m=65536,t=3,p=4$"


class SlowFakeHasher:
    """Remplace argon2 pour observer le fil d'exécution et le nombre de calculs simultanés."""

    # requires : `delay_seconds` ≥ 0.
    # assigns  : initialise les compteurs de l'instance.
    # ensures  : l'instance ne calcule rien de réel et dure `delay_seconds` par appel.
    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self._running = 0
        self.peak = 0
        self.threads: set[int] = set()

    # requires : rien.
    # assigns  : met à jour les compteurs de l'instance, protégés par un verrou.
    # ensures  : renvoie une empreinte factice après avoir bloqué le fil courant.
    def hash(self, _plain: str) -> str:
        with self._lock:
            self._running += 1
            self.peak = max(self.peak, self._running)
            self.threads.add(threading.get_ident())
        time.sleep(self._delay_seconds)
        with self._lock:
            self._running -= 1
        return "$argon2id$factice"


# requires : `max_concurrency` ≥ 1, `queue_timeout_seconds` > 0.
# assigns  : rien.
# ensures  : renvoie un service de hachage réel, configuré pour le test.
def make_hasher(
    max_concurrency: int = 4, queue_timeout_seconds: float = 10.0
) -> PasswordHasherService:
    return PasswordHasherService(
        max_concurrency=max_concurrency, queue_timeout_seconds=queue_timeout_seconds
    )


# requires : rien.
# assigns  : rien.
# ensures  : l'empreinte utilise argon2id avec au moins les paramètres imposés par CLAUDE.md.
async def test_hash_uses_required_argon2id_parameters() -> None:
    password_hash = await make_hasher().hash(PASSPHRASE)

    assert password_hash.startswith(REQUIRED_PREFIX)
    assert PASSPHRASE not in password_hash


# requires : rien.
# assigns  : rien.
# ensures  : chaque appel tire un nouveau sel : deux comptes au même mot de passe diffèrent.
async def test_hash_uses_a_new_salt_each_time() -> None:
    hasher = make_hasher()

    assert await hasher.hash(PASSPHRASE) != await hasher.hash(PASSPHRASE)


# requires : rien.
# assigns  : rien.
# ensures  : la vérification n'accepte que le mot de passe exact.
async def test_verify_accepts_only_the_right_password() -> None:
    hasher = make_hasher()
    password_hash = await hasher.hash(PASSPHRASE)

    assert await hasher.verify(password_hash, PASSPHRASE) is True
    assert await hasher.verify(password_hash, PASSPHRASE + " ") is False
    assert await hasher.verify(password_hash, PASSPHRASE.upper()) is False


# requires : rien.
# assigns  : remplace argon2 par un faux calcul le temps du test.
# ensures  : le calcul s'exécute hors du fil de la boucle d'événements.
async def test_hashing_runs_outside_event_loop_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    hasher = make_hasher()
    fake = SlowFakeHasher(delay_seconds=0)
    monkeypatch.setattr(hasher, "_hasher", fake)

    await hasher.hash(PASSPHRASE)

    assert threading.get_ident() not in fake.threads


# requires : rien.
# assigns  : remplace argon2 par un faux calcul le temps du test.
# ensures  : jamais plus de calculs simultanés que la borne configurée, même sous la charge.
async def test_concurrent_hashes_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    hasher = make_hasher(max_concurrency=2)
    fake = SlowFakeHasher(delay_seconds=0.05)
    monkeypatch.setattr(hasher, "_hasher", fake)

    await asyncio.gather(*(hasher.hash(PASSPHRASE) for _ in range(8)))

    assert fake.peak == 2


# requires : rien.
# assigns  : remplace argon2 par un faux calcul le temps du test.
# ensures  : une demande qui attend trop longtemps est refusée au lieu de s'accumuler.
async def test_saturated_hasher_refuses_after_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    hasher = make_hasher(max_concurrency=1, queue_timeout_seconds=0.05)
    monkeypatch.setattr(hasher, "_hasher", SlowFakeHasher(delay_seconds=0.5))

    first = asyncio.create_task(hasher.hash(PASSPHRASE))
    await asyncio.sleep(0.01)
    with pytest.raises(PasswordHashingBusyError):
        await hasher.hash(PASSPHRASE)
    await first


# requires : `variable` est un réglage borné, `value` est hors de ses limites.
# assigns  : la variable d'environnement le temps du test.
# ensures  : une borne de hachage absurde empêche le démarrage.
@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("TASKHANDLER_PASSWORD_HASH_MAX_CONCURRENCY", "0"),
        ("TASKHANDLER_PASSWORD_HASH_MAX_CONCURRENCY", "1000"),
        ("TASKHANDLER_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS", "0"),
        ("TASKHANDLER_PASSWORD_HASH_QUEUE_TIMEOUT_SECONDS", "3600"),
    ],
)
def test_refuses_out_of_bounds_hashing_limits(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)
