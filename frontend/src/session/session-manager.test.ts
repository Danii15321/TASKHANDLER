// Tests du gestionnaire de session, contre une fausse API fidèle au backend : rotation du jeton
// de renouvellement, détection de réutilisation qui coupe toute la session, jeton d'accès de
// 15 minutes. Plusieurs « onglets » partagent le même cookie, comme dans un vrai navigateur.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { z } from "zod";

import { ApiError } from "@/api/errors";
import { createHttpClient, type FetchFunction } from "@/api/http-client";
import { createLocalLockManager, type LockManagerLike } from "@/session/locks";
import {
  createSessionManager,
  type SessionChannel,
  type SessionManager,
  type SessionMessage,
} from "@/session/session-manager";

const API_ORIGIN = "https://api.taskhandler.localhost:8443";
const ME_PATH = "/api/v1/users/me";
const ACCESS_TTL_MS = 15 * 60_000;
const CREDENTIALS = { email: "alice@exemple.org", password: "violet ruisseau lanterne 2026" };

// Jeton de renouvellement connu de la fausse API.
interface StoredRefreshToken {
  readonly family: number;
  state: "active" | "rotated" | "revoked";
}

// Fausse API : même logique de session que le backend, cookie partagé entre onglets.
class FakeApi {
  // Le cookie `HttpOnly` du navigateur : partagé par tous les onglets, invisible du code testé.
  cookie: string | undefined;
  readonly refreshTokens = new Map<string, StoredRefreshToken>();
  readonly accessTokens = new Map<string, { family: number; expiresAt: number }>();
  readonly revokedFamilies = new Set<number>();
  reuseDetected = 0;
  refreshCalls = 0;
  available = true;
  // Délai renvoyé en `429` par le renouvellement, ou `undefined` pour ne pas limiter.
  refreshRetryAfter: number | undefined;
  private counter = 0;

  // requires : rien.
  // assigns  : rien.
  // ensures  : renvoie le nombre de sessions encore actives.
  activeFamilies(): number {
    const families = [...this.refreshTokens.values()]
      .filter((token) => token.state === "active")
      .map((token) => token.family);
    return new Set(families).size;
  }

  // requires : `family` identifie une session.
  // assigns  : enregistre un nouveau jeton de renouvellement et un jeton d'accès ; pose le cookie.
  // ensures  : renvoie la réponse de jetons, au format du contrat.
  private issue(family: number): Response {
    this.counter += 1;
    const refreshToken = `refresh-${String(this.counter)}`;
    const accessToken = `access${String(this.counter)}.payload.signature`;
    this.refreshTokens.set(refreshToken, { family, state: "active" });
    this.accessTokens.set(accessToken, { family, expiresAt: Date.now() + ACCESS_TTL_MS });
    this.cookie = refreshToken;
    return json({ access_token: accessToken, token_type: "bearer", expires_in: 900 });
  }

  // requires : `family` identifie une session.
  // assigns  : révoque tous les jetons de la session.
  // ensures  : plus aucun jeton de cette session n'est accepté.
  private revoke(family: number): void {
    this.revokedFamilies.add(family);
    for (const token of this.refreshTokens.values()) {
      if (token.family === family) {
        token.state = "revoked";
      }
    }
  }

  // requires : `init` vient du client HTTP.
  // assigns  : rien.
  // ensures  : renvoie la session du jeton d'accès s'il est valide, `undefined` sinon.
  private bearerFamily(init: RequestInit): number | undefined {
    const header = new Headers(init.headers).get("Authorization") ?? "";
    const access = this.accessTokens.get(header.replace(/^Bearer /, ""));
    if (access === undefined || access.expiresAt <= Date.now()) {
      return undefined;
    }
    return this.revokedFamilies.has(access.family) ? undefined : access.family;
  }

  // requires : voir `fetch`.
  // assigns  : l'état de la fausse API.
  // ensures  : répond comme le backend.
  readonly fetch: FetchFunction = async (input, init) => {
    // Le navigateur joint le cookie au moment de l'envoi, avant toute réponse : deux requêtes
    // parties ensemble emportent donc le même cookie.
    const cookie = init.credentials === "include" ? this.cookie : undefined;
    await Promise.resolve();
    await Promise.resolve();
    if (!this.available) {
      throw new TypeError("Failed to fetch");
    }
    const path = new URL(input).pathname;

    if (path === "/api/v1/auth/login") {
      const body = z
        .object({ email: z.string(), password: z.string() })
        .parse(JSON.parse(typeof init.body === "string" ? init.body : "null"));
      if (body.password !== CREDENTIALS.password) {
        return problem(401);
      }
      return this.issue(this.counter + 1000);
    }
    if (path === "/api/v1/auth/refresh") {
      this.refreshCalls += 1;
      expect(new Headers(init.headers).get("X-CSRF-Protection")).toBe("1");
      if (this.refreshRetryAfter !== undefined) {
        const limited = problem(429);
        limited.headers.set("retry-after", String(this.refreshRetryAfter));
        return limited;
      }
      const stored = cookie === undefined ? undefined : this.refreshTokens.get(cookie);
      if (stored === undefined || stored.state === "revoked") {
        return problem(401);
      }
      if (stored.state === "rotated") {
        // Réutilisation : exactement comme le backend, toute la session est coupée.
        this.reuseDetected += 1;
        this.revoke(stored.family);
        return problem(401);
      }
      stored.state = "rotated";
      return this.issue(stored.family);
    }
    if (path === "/api/v1/auth/logout" || path === "/api/v1/auth/logout-all") {
      const family = this.bearerFamily(init);
      if (family === undefined) {
        return problem(401);
      }
      const families = path.endsWith("logout-all")
        ? [...this.refreshTokens.values()].map((t) => t.family)
        : [family];
      for (const closed of families) {
        this.revoke(closed);
      }
      if (init.credentials === "include") {
        this.cookie = undefined;
      }
      return new Response(null, { status: 204 });
    }
    if (path === ME_PATH) {
      return this.bearerFamily(init) === undefined ? problem(401) : json({ ok: true });
    }
    return problem(404);
  };
}

// requires : `body` est sérialisable.
// assigns  : rien.
// ensures  : renvoie une réponse JSON `200`.
function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
}

// requires : `status` est un code d'erreur.
// assigns  : rien.
// ensures  : renvoie une erreur au format RFC 9457.
function problem(status: number): Response {
  const body = { type: "about:blank", title: "Erreur", status, request_id: "r1" };
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/problem+json" },
  });
}

// Bus entre onglets : comme `BroadcastChannel`, un message va à tous les autres, pas à l'émetteur.
class FakeBus {
  private readonly listeners = new Set<{ listener: (data: unknown) => void }>();

  // requires : rien.
  // assigns  : rien à la création.
  // ensures  : renvoie le canal d'un nouvel onglet relié au bus.
  connect(): SessionChannel & { inject(data: unknown): void } {
    const own: { listener: (data: unknown) => void } = { listener: () => undefined };
    const listeners = this.listeners;
    return {
      // requires : `message` est un message de session.
      // assigns  : rien.
      // ensures  : délivre `message` aux autres onglets, après la tâche en cours.
      postMessage(message: SessionMessage): void {
        for (const entry of listeners) {
          if (entry !== own) {
            queueMicrotask(() => {
              entry.listener(message);
            });
          }
        }
      },
      // requires : `listener` traite un message.
      // assigns  : inscrit l'onglet sur le bus.
      // ensures  : renvoie la désinscription.
      onMessage(listener): () => void {
        own.listener = listener;
        listeners.add(own);
        return () => {
          listeners.delete(own);
        };
      },
      // requires : rien.
      // assigns  : rien.
      // ensures  : délivre `data` à cet onglet, comme un message venu d'ailleurs.
      inject(data: unknown): void {
        own.listener(data);
      },
    };
  }
}

// requires : rien.
// assigns  : programme un minuteur.
// ensures  : renvoie la fonction qui l'annule.
function schedule(callback: () => void, delayMs: number): () => void {
  const handle = setTimeout(callback, delayMs);
  return () => {
    clearTimeout(handle);
  };
}

const managers: SessionManager[] = [];

// requires : `api` est la fausse API ; `locks` les verrous, partagés ou non entre onglets.
// assigns  : ajoute l'onglet à `managers`, fermés après chaque test.
// ensures  : renvoie un « onglet » : son gestionnaire de session et son canal.
function openTab(
  api: FakeApi,
  locks: LockManagerLike,
  bus: FakeBus,
): { session: SessionManager; channel: ReturnType<FakeBus["connect"]> } {
  const channel = bus.connect();
  const session = createSessionManager({
    http: createHttpClient({ apiOrigin: API_ORIGIN, fetch: api.fetch }),
    locks,
    channel,
    schedule,
  });
  managers.push(session);
  return { session, channel };
}

// requires : `session` est un gestionnaire de session.
// assigns  : envoie une requête authentifiée.
// ensures  : renvoie la réponse de la route protégée.
function readMe(session: SessionManager): Promise<unknown> {
  return session.request({ method: "GET", path: ME_PATH, schema: z.unknown() });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  for (const manager of managers.splice(0)) {
    manager.dispose();
  }
  vi.useRealTimers();
});

describe("gestionnaire de session", () => {
  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : sans cookie, la restauration conclut à l'absence de session.
  test("conclut à l'absence de session sans cookie", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());

    await session.restore();

    expect(session.getSnapshot().status).toBe("anonymous");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : la connexion ouvre la session ; l'état public ne contient jamais le jeton.
  test("ouvre la session sans jamais exposer le jeton", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());

    await session.login(CREDENTIALS);

    expect(session.getSnapshot()).toEqual({ status: "authenticated" });
    expect(JSON.stringify(session.getSnapshot())).not.toContain("access");
    await expect(readMe(session)).resolves.toEqual({ ok: true });
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : des identifiants hors des limites de l'API sont refusés sans requête.
  test("refuse des identifiants hors limites sans appeler l'API", async () => {
    const api = new FakeApi();
    const fetch = vi.spyOn(api, "fetch");
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());

    await expect(session.login({ email: "", password: "x" })).rejects.toMatchObject({
      kind: "validation",
    });
    expect(fetch).not.toHaveBeenCalled();
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : deux onglets qui se chargent en même temps restaurent tous deux la session, sans
  //            que l'API ne voie jamais deux fois le même jeton.
  test("sérialise les restaurations simultanées de deux onglets", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    await openTab(api, locks, bus).session.login(CREDENTIALS);
    const first = openTab(api, locks, bus).session;
    const second = openTab(api, locks, bus).session;

    await Promise.all([first.restore(), second.restore()]);

    expect(first.getSnapshot().status).toBe("authenticated");
    expect(second.getSnapshot().status).toBe("authenticated");
    expect(api.reuseDetected).toBe(0);
    expect(api.activeFamilies()).toBe(1);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : témoin : sans verrou partagé, les mêmes onglets déclenchent la détection de
  //            réutilisation et perdent la session. Le verrou est donc indispensable.
  test("sans verrou partagé, deux onglets coupent la session", async () => {
    const api = new FakeApi();
    const bus = new FakeBus();
    await openTab(api, createLocalLockManager(), bus).session.login(CREDENTIALS);
    const first = openTab(api, createLocalLockManager(), bus).session;
    const second = openTab(api, createLocalLockManager(), bus).session;

    await Promise.all([first.restore(), second.restore()]);

    expect(api.reuseDetected).toBe(1);
    expect(api.activeFamilies()).toBe(0);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : deux restaurations dans le même onglet (mode strict de React) ne font qu'un seul
  //            renouvellement.
  test("ne renouvelle qu'une fois pour deux restaurations du même onglet", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    await openTab(api, locks, bus).session.login(CREDENTIALS);
    const tab = openTab(api, locks, bus).session;

    await Promise.all([tab.restore(), tab.restore()]);
    await tab.restore();

    expect(api.refreshCalls).toBe(1);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : deux onglets ouverts plus de 15 minutes gardent leur session : chacun renouvelle
  //            avant l'expiration, sous le verrou, sans réutilisation.
  test("garde la session de deux onglets au-delà de 15 minutes", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    const first = openTab(api, locks, bus).session;
    await first.login(CREDENTIALS);
    const second = openTab(api, locks, bus).session;
    await second.restore();

    await vi.advanceTimersByTimeAsync(31 * 60_000);

    await expect(readMe(first)).resolves.toEqual({ ok: true });
    await expect(readMe(second)).resolves.toEqual({ ok: true });
    expect(api.reuseDetected).toBe(0);
    expect(api.refreshCalls).toBeGreaterThanOrEqual(5);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : un jeton expiré sans renouvellement programmé (onglet en veille) est renouvelé à
  //            la première requête refusée, qui est rejouée une seule fois.
  test("renouvelle puis rejoue une requête refusée pour jeton expiré", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());
    await session.login(CREDENTIALS);
    for (const token of api.accessTokens.values()) {
      token.expiresAt = 0;
    }

    await expect(readMe(session)).resolves.toEqual({ ok: true });
    expect(api.refreshCalls).toBe(1);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : une session révoquée ailleurs est perdue proprement : une seule tentative de
  //            renouvellement, aucune boucle, état `anonymous`.
  test("abandonne sans boucle une session révoquée ailleurs", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());
    await session.login(CREDENTIALS);
    for (const token of api.refreshTokens.values()) {
      token.state = "revoked";
    }
    for (const token of api.accessTokens.values()) {
      token.expiresAt = 0;
    }

    await expect(readMe(session)).rejects.toMatchObject({ kind: "unauthorized" });
    await expect(readMe(session)).rejects.toBeInstanceOf(ApiError);
    expect(session.getSnapshot().status).toBe("anonymous");
    expect(api.refreshCalls).toBe(2);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : la déconnexion d'un onglet ferme la session côté API, efface le cookie, et les
  //            autres onglets oublient aussitôt la leur.
  test("propage la déconnexion à tous les onglets", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    const first = openTab(api, locks, bus).session;
    await first.login(CREDENTIALS);
    const second = openTab(api, locks, bus).session;
    await second.restore();

    await first.logout();
    await vi.advanceTimersByTimeAsync(0);

    expect(first.getSnapshot().status).toBe("anonymous");
    expect(second.getSnapshot().status).toBe("anonymous");
    expect(api.cookie).toBeUndefined();
    expect(api.activeFamilies()).toBe(0);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : un onglet tout juste rechargé, sans jeton en mémoire, peut quand même fermer
  //            toutes les sessions : il renouvelle d'abord pour prouver la demande.
  test("ferme toutes les sessions depuis un onglet qui n'a pas encore de jeton", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    await openTab(api, locks, bus).session.login(CREDENTIALS);
    await openTab(api, locks, bus).session.login(CREDENTIALS);
    const reloaded = openTab(api, locks, bus).session;

    await reloaded.logoutAll();

    expect(api.activeFamilies()).toBe(0);
    expect(reloaded.getSnapshot().status).toBe("anonymous");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : si l'API ne répond pas, la session locale est quand même effacée, et l'erreur est
  //            relayée pour que l'interface signale une déconnexion incomplète.
  test("efface la session locale même si la déconnexion échoue", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());
    await session.login(CREDENTIALS);
    api.available = false;

    await expect(session.logout()).rejects.toMatchObject({ kind: "network" });
    expect(session.getSnapshot().status).toBe("anonymous");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : une connexion dans un onglet ouvre la session des onglets qui n'en avaient pas.
  test("propage la connexion aux autres onglets", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    const idle = openTab(api, locks, bus).session;
    await idle.restore();

    await openTab(api, locks, bus).session.login(CREDENTIALS);
    await vi.advanceTimersByTimeAsync(0);

    expect(idle.getSnapshot().status).toBe("authenticated");
    expect(api.reuseDetected).toBe(0);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : un message mal formé sur le canal est ignoré.
  test.each([null, "logout", { type: "logout", token: "x" }, { type: "reset" }])(
    "ignore le message %j",
    async (message) => {
      const api = new FakeApi();
      const { session, channel } = openTab(api, createLocalLockManager(), new FakeBus());
      await session.login(CREDENTIALS);

      channel.inject(message);

      expect(session.getSnapshot().status).toBe("authenticated");
    },
  );

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : une panne réseau ne fait pas perdre la session : l'état devient `unreachable`,
  //            puis un nouvel essai la rétablit.
  test("retente le renouvellement après une panne passagère", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    await openTab(api, locks, bus).session.login(CREDENTIALS);
    const tab = openTab(api, locks, bus).session;
    api.available = false;

    await tab.restore();
    expect(tab.getSnapshot().status).toBe("unreachable");

    api.available = true;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(tab.getSnapshot().status).toBe("authenticated");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : une déconnexion annoncée pendant un renouvellement l'emporte sur sa réponse.
  test("ne laisse pas un renouvellement tardif annuler une déconnexion", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    await openTab(api, locks, bus).session.login(CREDENTIALS);
    const { session, channel } = openTab(api, locks, bus);

    const restoring = session.restore();
    channel.inject({ type: "logout" });
    await restoring;

    expect(session.getSnapshot().status).toBe("anonymous");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : une erreur autre qu'un `401` est relayée telle quelle, sans renouvellement.
  test("relaie une erreur autre qu'un 401 sans renouveler", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());
    await session.login(CREDENTIALS);

    await expect(
      session.request({ method: "GET", path: "/api/v1/inconnue", schema: z.unknown() }),
    ).rejects.toMatchObject({ kind: "not_found" });
    expect(api.refreshCalls).toBe(0);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : sans session, une requête authentifiée échoue en `unauthorized` sans être envoyée.
  test("refuse une requête authentifiée sans session", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());

    await expect(readMe(session)).rejects.toMatchObject({ kind: "unauthorized" });
    expect(session.getSnapshot().status).toBe("anonymous");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : une déconnexion annoncée pendant un renouvellement en échec n'est pas écrasée par
  //            le traitement de cet échec.
  test("ignore l'échec d'un renouvellement interrompu par une déconnexion", async () => {
    const api = new FakeApi();
    const { session, channel } = openTab(api, createLocalLockManager(), new FakeBus());
    api.available = false;

    const restoring = session.restore();
    channel.inject({ type: "logout" });
    await restoring;

    expect(session.getSnapshot().status).toBe("anonymous");
    expect(vi.getTimerCount()).toBe(0);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : un renouvellement programmé qui échoue passagèrement ne fait pas perdre une
  //            session active ; le nouvel essai la prolonge.
  test("garde une session active malgré un renouvellement programmé en échec", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());
    await session.login(CREDENTIALS);
    api.available = false;

    await vi.advanceTimersByTimeAsync(14 * 60_000);
    expect(session.getSnapshot().status).toBe("authenticated");

    api.available = true;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(api.refreshCalls).toBe(1);
    await expect(readMe(session)).resolves.toEqual({ ok: true });
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : un renouvellement limité (`429`) attend le délai demandé par l'API avant de
  //            réessayer, sans la marteler.
  test("respecte le Retry-After d'un renouvellement limité", async () => {
    const api = new FakeApi();
    const locks = createLocalLockManager();
    const bus = new FakeBus();
    await openTab(api, locks, bus).session.login(CREDENTIALS);
    const tab = openTab(api, locks, bus).session;
    api.refreshRetryAfter = 120;

    await tab.restore();
    api.refreshRetryAfter = undefined;
    await vi.advanceTimersByTimeAsync(119_000);
    expect(api.refreshCalls).toBe(1);

    await vi.advanceTimersByTimeAsync(1_000);
    expect(api.refreshCalls).toBe(2);
    expect(tab.getSnapshot().status).toBe("authenticated");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : se déconnecter sans session n'appelle pas la route de déconnexion.
  test("se déconnecte sans session sans appeler l'API de déconnexion", async () => {
    const api = new FakeApi();
    const fetch = vi.spyOn(api, "fetch");
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());

    await session.logout();

    expect(fetch.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
      "/api/v1/auth/refresh",
    ]);
    expect(session.getSnapshot().status).toBe("anonymous");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : une erreur imprévue (hors `ApiError`) est traitée comme passagère : réessai.
  test("traite une erreur imprévue comme passagère", async () => {
    const api = new FakeApi();
    const failing: LockManagerLike = {
      request: () => Promise.reject(new Error("verrou indisponible")),
    };
    const { session } = openTab(api, failing, new FakeBus());

    await session.restore();

    expect(session.getSnapshot().status).toBe("unreachable");
    expect(vi.getTimerCount()).toBe(1);
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : les abonnés sont prévenus des changements, jusqu'à leur désabonnement.
  test("prévient les abonnés jusqu'au désabonnement", async () => {
    const api = new FakeApi();
    const { session } = openTab(api, createLocalLockManager(), new FakeBus());
    const listener = vi.fn();
    const unsubscribe = session.subscribe(listener);

    await session.login(CREDENTIALS);
    unsubscribe();
    await session.logout();

    expect(listener).toHaveBeenCalledTimes(1);
  });
});
