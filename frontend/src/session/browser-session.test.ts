// Tests de l'assemblage navigateur : verrous, canal entre onglets, gestionnaire unique.
import { afterEach, describe, expect, test, vi } from "vitest";

import type { LockManagerLike } from "@/session/locks";

const API_ORIGIN = "https://api.taskhandler.localhost:8443";

// requires : rien.
// assigns  : variables d'environnement et cache des modules du test.
// ensures  : renvoie le module rechargé avec une configuration publique valide.
async function loadModule(): Promise<typeof import("@/session/browser-session")> {
  vi.resetModules();
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", API_ORIGIN);
  return import("@/session/browser-session");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("assemblage navigateur", () => {
  // requires : rien.
  // assigns  : remplace `navigator` le temps du test.
  // ensures  : avec Web Locks, le verrou demandé est celui du navigateur, partagé entre onglets.
  test("utilise Web Locks quand le navigateur l'offre", async () => {
    const request = vi.fn((_name: string, callback: () => Promise<string>) => callback());
    vi.stubGlobal("navigator", { locks: { request } });
    const { browserLocks } = await loadModule();

    await expect(browserLocks().request("verrou", () => Promise.resolve("fait"))).resolves.toBe(
      "fait",
    );
    expect(request).toHaveBeenCalledWith("verrou", expect.any(Function));
  });

  // requires : rien.
  // assigns  : remplace `navigator` le temps du test.
  // ensures  : sans Web Locks, un verrou propre à l'onglet sérialise quand même ses tâches.
  test("se replie sur un verrou local sans Web Locks", async () => {
    vi.stubGlobal("navigator", {});
    const { browserLocks } = await loadModule();
    const locks: LockManagerLike = browserLocks();
    const order: string[] = [];

    await Promise.all([
      locks.request("verrou", async () => {
        await Promise.resolve();
        order.push("premier");
      }),
      locks.request("verrou", () => {
        order.push("second");
        return Promise.resolve();
      }),
    ]);

    expect(order).toEqual(["premier", "second"]);
  });

  // requires : rien.
  // assigns  : ouvre deux canaux, fermés à la fin.
  // ensures  : un message posté par un onglet arrive à l'autre, et plus après désabonnement.
  test("relie les onglets par BroadcastChannel", async () => {
    const { browserChannel } = await loadModule();
    const sender = browserChannel();
    const receiver = browserChannel();
    const received: unknown[] = [];
    const stop = receiver.onMessage((data) => received.push(data));

    sender.postMessage({ type: "logout" });
    await vi.waitFor(() => {
      expect(received).toEqual([{ type: "logout" }]);
    });
    stop();
    sender.postMessage({ type: "login" });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(received).toEqual([{ type: "logout" }]);
  });

  // requires : rien.
  // assigns  : crée le gestionnaire de l'onglet.
  // ensures  : l'onglet n'a qu'un gestionnaire, donc qu'un seul endroit où vit le jeton.
  test("ne crée qu'un gestionnaire par onglet", async () => {
    vi.stubGlobal("navigator", {});
    const { getBrowserSessionManager } = await loadModule();

    const manager = getBrowserSessionManager();

    expect(getBrowserSessionManager()).toBe(manager);
    expect(manager.getSnapshot()).toEqual({ status: "unknown" });
    manager.dispose();
  });

  // requires : rien.
  // assigns  : remplace `fetch` le temps du test.
  // ensures  : la restauration passe par le vrai `fetch`, vers la route de renouvellement de
  //            l'API configurée, avec le cookie ; un refus laisse l'onglet sans session.
  test("restaure la session avec le fetch du navigateur", async () => {
    vi.stubGlobal("navigator", {});
    const body = { type: "about:blank", title: "Unauthorized", status: 401, request_id: "r" };
    const fetch = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 401,
          headers: { "content-type": "application/problem+json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    const { getBrowserSessionManager } = await loadModule();
    const manager = getBrowserSessionManager();

    await manager.restore();

    expect(fetch).toHaveBeenCalledWith(
      `${API_ORIGIN}/api/v1/auth/refresh`,
      expect.objectContaining({ credentials: "include", method: "POST" }),
    );
    expect(manager.getSnapshot().status).toBe("anonymous");
    manager.dispose();
  });

  // requires : rien.
  // assigns  : programme puis annule un minuteur.
  // ensures  : une connexion programme le renouvellement, et la déconnexion l'annule.
  test("programme et annule le renouvellement avec les minuteurs du navigateur", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("navigator", {});
    const tokens = { access_token: "a.b.c", token_type: "bearer", expires_in: 900 };
    const fetch = vi.fn((url: string) =>
      Promise.resolve(
        url.endsWith("/logout")
          ? new Response(null, { status: 204 })
          : new Response(JSON.stringify(tokens), {
              headers: { "content-type": "application/json" },
            }),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    const { getBrowserSessionManager } = await loadModule();
    const manager = getBrowserSessionManager();

    await manager.login({ email: "alice@exemple.org", password: "violet ruisseau lanterne" });
    expect(vi.getTimerCount()).toBe(1);
    await manager.logout();

    expect(vi.getTimerCount()).toBe(0);
    manager.dispose();
    vi.useRealTimers();
  });
});
