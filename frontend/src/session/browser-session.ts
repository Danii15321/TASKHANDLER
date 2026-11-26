// Assemblage du gestionnaire de session pour le navigateur : vrai `fetch`, verrous Web Locks
// partagés entre onglets, et `BroadcastChannel` pour annoncer connexion et déconnexion.
import { createHttpClient, type HttpClient } from "@/api/http-client";
import { loadPublicConfig } from "@/config/public-config";
import { createLocalLockManager, type LockManagerLike } from "@/session/locks";
import {
  createSessionManager,
  SESSION_CHANNEL_NAME,
  type SessionChannel,
  type SessionManager,
} from "@/session/session-manager";

// Gestionnaire unique de l'onglet : le jeton d'accès n'existe qu'à un seul endroit.
let instance: SessionManager | undefined;
// Client HTTP unique de l'onglet, partagé par le gestionnaire et les formulaires publics.
let httpInstance: HttpClient | undefined;

// requires : exécuté dans un navigateur.
// assigns  : crée le client au premier appel.
// ensures  : renvoie toujours le même client, relié à l'API configurée et au `fetch` du
//            navigateur. Les requêtes sans jeton (inscription, liens reçus par e-mail) passent
//            par lui ; les requêtes authentifiées passent par le gestionnaire de session.
export function getBrowserHttpClient(): HttpClient {
  httpInstance ??= createHttpClient({
    apiOrigin: loadPublicConfig().apiOrigin,
    // requires : voir `fetch`.
    // assigns  : envoie une requête HTTP.
    // ensures  : délègue au `fetch` du navigateur, appelé sans `this` détourné.
    fetch: (input, init) => fetch(input, init),
  });
  return httpInstance;
}

// requires : exécuté dans un navigateur.
// assigns  : rien.
// ensures  : renvoie les verrous partagés entre onglets ; à défaut de Web Locks (navigateur
//            ancien), des verrous propres à l'onglet. Le pire cas est alors une session coupée
//            par la détection de réutilisation, jamais une faille.
export function browserLocks(): LockManagerLike {
  if ("locks" in navigator) {
    const locks = navigator.locks;
    return {
      // requires : voir `LockManagerLike`.
      // assigns  : prend le verrou `name` dans tous les onglets de l'origine.
      // ensures  : exécute `callback` seul, puis libère le verrou.
      request: <T>(name: string, callback: () => Promise<T>): Promise<T> =>
        locks.request(name, callback),
    };
  }
  return createLocalLockManager();
}

// requires : exécuté dans un navigateur.
// assigns  : ouvre un `BroadcastChannel`.
// ensures  : renvoie le canal entre onglets de la même origine. Le navigateur ne le relie jamais
//            à une autre origine : un autre site ne peut ni écouter ni écrire.
export function browserChannel(): SessionChannel {
  const channel = new BroadcastChannel(SESSION_CHANNEL_NAME);
  return {
    // requires : `message` est un message de session, sans jeton.
    // assigns  : envoie `message` aux autres onglets.
    // ensures  : les autres onglets le reçoivent ; celui-ci non.
    postMessage(message): void {
      channel.postMessage(message);
    },
    // requires : `listener` traite un message non validé.
    // assigns  : ajoute un écouteur au canal.
    // ensures  : renvoie la fonction qui retire l'écouteur.
    onMessage(listener): () => void {
      // requires : `event` vient du canal.
      // assigns  : rien.
      // ensures  : transmet le contenu brut, validé ensuite par le gestionnaire.
      const handler = (event: MessageEvent): void => {
        listener(event.data);
      };
      channel.addEventListener("message", handler);
      return () => {
        channel.removeEventListener("message", handler);
      };
    },
  };
}

// requires : exécuté dans un navigateur, jamais pendant le rendu serveur.
// assigns  : crée le gestionnaire de l'onglet au premier appel.
// ensures  : renvoie toujours le même gestionnaire pour cet onglet.
export function getBrowserSessionManager(): SessionManager {
  instance ??= createSessionManager({
    http: getBrowserHttpClient(),
    locks: browserLocks(),
    channel: browserChannel(),
    // requires : `delayMs` est positif.
    // assigns  : programme un minuteur.
    // ensures  : renvoie la fonction qui l'annule.
    schedule: (callback, delayMs) => {
      const handle = setTimeout(callback, delayMs);
      return () => {
        clearTimeout(handle);
      };
    },
  });
  return instance;
}
