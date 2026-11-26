import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import type { SessionManager } from "@/session/session-manager";

// Espions conservés à part : les lire sur l'objet passerait par des méthodes détachées.
const subscribe = vi.fn(() => () => undefined);
const getSnapshot = vi.fn(() => ({ status: "authenticated" as const }));
const restore = vi.fn(() => Promise.resolve());
const manager = { subscribe, getSnapshot, restore } as unknown as SessionManager;

vi.mock("@/session/browser-session", () => ({ getBrowserSessionManager: () => manager }));

const { restoreBrowserSession, sessionStore, useSession } = await import("@/session/use-session");
const { SessionBootstrap } = await import("@/session/session-bootstrap");

afterEach(() => {
  vi.clearAllMocks();
});

// requires : rendu par `renderToStaticMarkup`.
// assigns  : rien.
// ensures  : affiche l'état de session vu pendant le rendu.
function Status(): ReactNode {
  return <p>{useSession().status}</p>;
}

describe("liaison React de la session", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : le rendu serveur ne connaît jamais la session : il voit toujours `unknown`, et
  //            l'amorce de session n'affiche rien ni ne restaure quoi que ce soit côté serveur.
  test("le rendu serveur ne connaît pas la session", () => {
    expect(renderToStaticMarkup(<Status />)).toBe("<p>unknown</p>");
    expect(renderToStaticMarkup(<SessionBootstrap />)).toBe("");
    expect(restore).not.toHaveBeenCalled();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : côté navigateur, la source lit et écoute le gestionnaire de l'onglet.
  test("la source lit et écoute le gestionnaire de l'onglet", () => {
    const listener = vi.fn();

    sessionStore.subscribe(listener);

    expect(subscribe).toHaveBeenCalledWith(listener);
    expect(sessionStore.getSnapshot()).toEqual({ status: "authenticated" });
    expect(sessionStore.getServerSnapshot()).toEqual({ status: "unknown" });
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : la restauration délègue au gestionnaire de l'onglet.
  test("la restauration délègue au gestionnaire", () => {
    restoreBrowserSession();

    expect(restore).toHaveBeenCalledTimes(1);
  });
});
