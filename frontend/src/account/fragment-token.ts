"use client";

// Jeton à usage unique reçu par e-mail (vérification d'adresse, réinitialisation).
//
// L'API place le jeton dans le fragment du lien (`/verify-email#token=…`) : un fragment n'est
// jamais envoyé au serveur, il n'apparaît donc ni dans les journaux d'accès ni dans l'en-tête
// `Referer`. Il reste en revanche dans la barre d'adresse et l'historique du navigateur : ce
// module l'en retire dès la première lecture, avant tout autre traitement, puis le garde en
// mémoire le temps de l'envoyer dans le corps d'un `POST`.
import { useSyncExternalStore } from "react";

import { ONE_TIME_TOKEN_PATTERN } from "@/api/schemas";

// Sous-ensembles de `window.location` et `window.history` utilisés ici.
export interface LocationLike {
  readonly hash: string;
  readonly pathname: string;
  readonly search: string;
}
export interface HistoryLike {
  readonly state: unknown;
  replaceState(data: unknown, unused: string, url?: string): void;
}

// requires : `location` et `history` sont ceux de la page.
// assigns  : remplace l'entrée courante de l'historique par l'adresse sans fragment.
// ensures  : renvoie le jeton s'il a exactement le format attendu, `null` sinon. Tout fragment,
//            valide ou non, est effacé de l'adresse et de l'historique avant la validation.
export function takeFragmentToken(location: LocationLike, history: HistoryLike): string | null {
  // Copie du fragment avant de l'effacer : `window.location` est un objet vivant, qui ne contient
  // plus le fragment une fois l'adresse remplacée.
  const hash = location.hash;
  if (hash === "") {
    return null;
  }
  // `replaceState` et non `pushState` : l'entrée contenant le jeton est remplacée, pas
  // conservée derrière le bouton « Précédent ».
  history.replaceState(history.state, "", `${location.pathname}${location.search}`);
  const token = new URLSearchParams(hash.slice(1)).get("token");
  return token !== null && ONE_TIME_TOKEN_PATTERN.test(token) ? token : null;
}

// Jeton capturé : `undefined` tant que la page n'a pas été lue, `null` sans jeton valide.
let captured: string | null | undefined;

// requires : exécuté dans le navigateur.
// assigns  : `captured` ; nettoie l'adresse si elle porte un fragment.
// ensures  : renvoie le jeton de la page. Un nouveau fragment (lien collé dans le même onglet)
//            remplace le précédent ; sinon la valeur déjà capturée est conservée.
function captureFromPage(): string | null {
  if (captured === undefined || window.location.hash !== "") {
    captured = takeFragmentToken(window.location, window.history);
  }
  return captured;
}

// requires : rien.
// assigns  : `captured`.
// ensures  : oublie le jeton : une fois utilisé, il ne reste pas en mémoire.
export function forgetFragmentToken(): void {
  captured = null;
}

// requires : `listener` est fourni par React ; exécuté dans le navigateur.
// assigns  : écoute les changements de fragment.
// ensures  : renvoie le désabonnement.
function subscribe(listener: () => void): () => void {
  window.addEventListener("hashchange", listener);
  return () => {
    window.removeEventListener("hashchange", listener);
  };
}

// requires : rien.
// assigns  : rien.
// ensures  : renvoie `undefined` : le serveur ne voit jamais le fragment.
function getServerSnapshot(): undefined {
  return undefined;
}

// requires : appelé dans un composant client.
// assigns  : nettoie l'adresse au premier rendu côté navigateur.
// ensures  : renvoie `undefined` pendant le rendu serveur, puis le jeton (`string`) ou `null`.
export function useFragmentToken(): string | null | undefined {
  return useSyncExternalStore(subscribe, captureFromPage, getServerSnapshot);
}

// requires : rien.
// assigns  : `captured`.
// ensures  : remet le module à son état initial (tests uniquement).
export function resetFragmentTokenForTests(): void {
  captured = undefined;
}
