// Verrous nommés : un seul renouvellement de session à la fois.
//
// L'API fait tourner le jeton de renouvellement à chaque usage, et traite le second usage d'un
// même jeton comme un vol : toute la session est alors coupée. Deux onglets qui renouvellent en
// même temps, avec le même cookie, déclencheraient exactement cela. Les renouvellements passent
// donc par un verrou partagé entre onglets (Web Locks), qui les exécute l'un après l'autre : le
// second part avec le cookie déjà renouvelé par le premier.

// Sous-ensemble de `navigator.locks` utilisé par le gestionnaire de session.
export interface LockManagerLike {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

// requires : rien.
// assigns  : rien à la création.
// ensures  : renvoie un gestionnaire de verrous propre à cet onglet, qui exécute les tâches d'un
//            même nom l'une après l'autre, dans l'ordre d'arrivée. Sert aux tests et de repli si
//            le navigateur n'offre pas Web Locks : la protection ne couvre alors qu'un onglet.
export function createLocalLockManager(): LockManagerLike {
  const queues = new Map<string, Promise<unknown>>();
  return {
    // requires : `callback` renvoie une promesse.
    // assigns  : allonge la file d'attente du verrou `name`.
    // ensures  : exécute `callback` après toutes les tâches déjà en file pour `name`, et renvoie
    //            son résultat ; l'échec d'une tâche ne bloque pas les suivantes.
    request<T>(name: string, callback: () => Promise<T>): Promise<T> {
      const previous = queues.get(name) ?? Promise.resolve();
      const run = previous.then(callback, callback);
      queues.set(
        name,
        run.catch(() => undefined),
      );
      return run;
    },
  };
}
