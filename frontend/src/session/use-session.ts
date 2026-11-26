"use client";

import { useEffect, useSyncExternalStore } from "react";

import { getBrowserSessionManager } from "@/session/browser-session";
import type { SessionSnapshot } from "@/session/session-manager";

// État rendu côté serveur : le serveur ne connaît jamais la session de l'utilisateur.
const SERVER_SNAPSHOT: SessionSnapshot = { status: "unknown" };

// Source externe lue par `useSyncExternalStore` : le gestionnaire de session de l'onglet.
// Des fonctions fléchées, et non des méthodes : React les appelle détachées de l'objet.
export const sessionStore = {
  // requires : `listener` est fourni par React ; exécuté dans le navigateur.
  // assigns  : abonne `listener` au gestionnaire de l'onglet.
  // ensures  : renvoie le désabonnement.
  subscribe: (listener: () => void): (() => void) => getBrowserSessionManager().subscribe(listener),
  // requires : exécuté dans le navigateur.
  // assigns  : rien.
  // ensures  : renvoie l'état courant de la session.
  getSnapshot: (): SessionSnapshot => getBrowserSessionManager().getSnapshot(),
  // requires : rien.
  // assigns  : rien.
  // ensures  : renvoie l'état utilisé pendant le rendu serveur.
  getServerSnapshot: (): SessionSnapshot => SERVER_SNAPSHOT,
};

// requires : exécuté dans le navigateur, après le premier rendu.
// assigns  : lance la restauration de la session.
// ensures  : un onglet rechargé retrouve sa session grâce au cookie, sans jeton stocké. Les
//            échecs sont déjà traduits en état (`anonymous`, `unreachable`) par le gestionnaire.
export function restoreBrowserSession(): void {
  void getBrowserSessionManager().restore();
}

// requires : appelé dans un composant client.
// assigns  : rien.
// ensures  : renvoie l'état de la session et provoque un nouveau rendu à chaque changement.
export function useSession(): SessionSnapshot {
  return useSyncExternalStore(
    sessionStore.subscribe,
    sessionStore.getSnapshot,
    sessionStore.getServerSnapshot,
  );
}

// requires : appelé une fois, dans la mise en page racine.
// assigns  : programme la restauration de la session au chargement de la page.
// ensures  : la restauration a lieu une fois par montage, côté navigateur seulement.
export function useSessionRestore(): void {
  useEffect(restoreBrowserSession, []);
}
