// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  forgetFragmentToken,
  resetFragmentTokenForTests,
  takeFragmentToken,
  type HistoryLike,
} from "@/account/fragment-token";

const TOKEN = "A".repeat(20) + "b-_9" + "z".repeat(19);

// requires : `hash` est le fragment de la page simulée.
// assigns  : rien.
// ensures  : renvoie une fausse page, son historique et l'espion de ses remplacements.
function page(hash: string): {
  location: { hash: string; pathname: string; search: string };
  history: HistoryLike;
  replaceState: ReturnType<typeof vi.fn<HistoryLike["replaceState"]>>;
} {
  const replaceState = vi.fn<HistoryLike["replaceState"]>();
  return {
    location: { hash, pathname: "/reset-password", search: "?source=mail" },
    history: { state: { key: 1 }, replaceState },
    replaceState,
  };
}

afterEach(() => {
  resetFragmentTokenForTests();
  window.history.replaceState(null, "", "/");
});

describe("takeFragmentToken", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : un jeton valide est renvoyé, et l'adresse est remplacée par la même sans fragment.
  test("lit le jeton et efface le fragment", () => {
    const { location, history, replaceState } = page(`#token=${TOKEN}`);

    expect(takeFragmentToken(location, history)).toBe(TOKEN);
    expect(replaceState).toHaveBeenCalledWith({ key: 1 }, "", "/reset-password?source=mail");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : un fragment invalide est effacé lui aussi, et aucun jeton n'est renvoyé.
  test.each(["#token=court", "#token=" + "!".repeat(43), "#autre=valeur", "#token="])(
    "efface aussi le fragment invalide %s",
    (hash) => {
      const { location, history, replaceState } = page(hash);

      expect(takeFragmentToken(location, history)).toBeNull();
      expect(replaceState).toHaveBeenCalledTimes(1);
    },
  );

  // requires : rien.
  // assigns  : rien.
  // ensures  : sans fragment, l'historique n'est pas touché.
  test("ne touche pas l'historique sans fragment", () => {
    const { location, history, replaceState } = page("");

    expect(takeFragmentToken(location, history)).toBeNull();
    expect(replaceState).not.toHaveBeenCalled();
  });

  // requires : rien.
  // assigns  : modifie l'adresse de la page jsdom.
  // ensures  : dans un vrai DOM, le jeton disparaît de l'adresse, et l'historique ne garde
  //            aucune entrée qui le contienne.
  test("retire le jeton de l'adresse réelle sans ajouter d'entrée à l'historique", () => {
    window.history.replaceState(null, "", `/verify-email#token=${TOKEN}`);
    const entries = window.history.length;

    expect(takeFragmentToken(window.location, window.history)).toBe(TOKEN);
    expect(window.location.href).not.toContain(TOKEN);
    expect(window.location.hash).toBe("");
    expect(window.history.length).toBe(entries);
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : oublier le jeton n'échoue jamais.
  test("oublie le jeton sur demande", () => {
    expect(() => {
      forgetFragmentToken();
    }).not.toThrow();
  });
});
