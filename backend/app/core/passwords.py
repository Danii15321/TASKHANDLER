"""Hachage des mots de passe avec argon2id, hors de la boucle d'événements et en nombre borné."""

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Annotated

import anyio.to_thread
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Request

# Paramètres minimum imposés par CLAUDE.md, au-dessus de la recommandation OWASP : 64 Mio de
# mémoire rendent une attaque par carte graphique coûteuse, car chaque essai doit les mobiliser.
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST_KIB = 65_536
_ARGON2_PARALLELISM = 4
_ARGON2_HASH_LENGTH = 32
_ARGON2_SALT_LENGTH = 16


class PasswordHashingBusyError(Exception):
    """Trop de hachages en attente : mieux vaut refuser que d'épuiser la mémoire du serveur."""


class PasswordHasherService:
    """Hache et vérifie les mots de passe, avec un nombre borné de calculs simultanés."""

    # requires : `max_concurrency` ≥ 1, `queue_timeout_seconds` > 0.
    # assigns  : crée l'algorithme configuré et le sémaphore de cette instance.
    # ensures  : l'instance n'exécute jamais plus de `max_concurrency` calculs à la fois.
    def __init__(self, *, max_concurrency: int, queue_timeout_seconds: float) -> None:
        self._hasher = PasswordHasher(
            time_cost=_ARGON2_TIME_COST,
            memory_cost=_ARGON2_MEMORY_COST_KIB,
            parallelism=_ARGON2_PARALLELISM,
            hash_len=_ARGON2_HASH_LENGTH,
            salt_len=_ARGON2_SALT_LENGTH,
            type=Type.ID,
        )
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._queue_timeout_seconds = queue_timeout_seconds

    # requires : `plain` a déjà passé la politique de mot de passe.
    # assigns  : occupe une place du sémaphore pendant le calcul.
    # ensures  : renvoie une empreinte argon2id au format PHC, avec un sel propre à cet appel ;
    #            lève `PasswordHashingBusyError` si aucune place ne se libère à temps.
    async def hash(self, plain: str) -> str:
        return await self._run_bounded(partial(self._hasher.hash, plain))

    # requires : `password_hash` est une empreinte produite par ce service.
    # assigns  : occupe une place du sémaphore pendant le calcul.
    # ensures  : renvoie `True` si le mot de passe correspond, `False` sinon ; lève
    #            `PasswordHashingBusyError` si aucune place ne se libère à temps.
    async def verify(self, password_hash: str, plain: str) -> bool:
        try:
            # La comparaison finale, à temps constant, est faite par la bibliothèque.
            return await self._run_bounded(partial(self._hasher.verify, password_hash, plain))
        except VerifyMismatchError:
            return False

    # requires : `job` est un calcul bloquant, sans effet sur l'état partagé.
    # assigns  : occupe une place du sémaphore jusqu'à la fin réelle du calcul.
    # ensures  : renvoie le résultat de `job`, calculé dans un fil d'exécution séparé ; lève
    #            `PasswordHashingBusyError` si l'attente dépasse le délai configuré.
    async def _run_bounded[T](self, job: Callable[[], T]) -> T:
        try:
            async with asyncio.timeout(self._queue_timeout_seconds):
                await self._semaphore.acquire()
        except TimeoutError:
            raise PasswordHashingBusyError from None
        try:
            # Hors de la boucle d'événements : un hachage dure environ 50 ms, pendant lesquelles
            # aucune autre requête ne serait traitée. `abandon_on_cancel=False` : si le client se
            # déconnecte, la place n'est libérée qu'à la fin réelle du calcul, et la borne tient.
            return await anyio.to_thread.run_sync(job, abandon_on_cancel=False)
        finally:
            self._semaphore.release()


# requires : l'application a été assemblée par `create_app`.
# assigns  : rien.
# ensures  : renvoie le service de hachage partagé par toute l'application.
def get_password_hasher(request: Request) -> PasswordHasherService:
    hasher: PasswordHasherService = request.app.state.password_hasher
    return hasher


PasswordHasherDependency = Annotated[PasswordHasherService, Depends(get_password_hasher)]
