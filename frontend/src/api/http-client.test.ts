import { describe, expect, test, vi } from "vitest";
import { z } from "zod";

import { ApiError } from "@/api/errors";
import { createHttpClient, type FetchFunction } from "@/api/http-client";
import { TokenResponseSchema } from "@/api/schemas";

const API_ORIGIN = "https://api.taskhandler.localhost:8443";
const TOKEN = "aaa.bbb.ccc";
const TOKENS = { access_token: TOKEN, token_type: "bearer", expires_in: 900 };

// requires : `body` est sérialisable en JSON.
// assigns  : rien.
// ensures  : renvoie une réponse JSON de l'API, du code et des en-têtes donnés.
function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  const contentType = status >= 400 ? "application/problem+json" : "application/json";
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": contentType, ...headers },
  });
}

// requires : `response` est la réponse que doit renvoyer le faux `fetch`.
// assigns  : rien.
// ensures  : renvoie un client relié à un faux `fetch`, et ce faux `fetch` pour l'inspecter.
function clientReturning(response: Response | Error): {
  client: ReturnType<typeof createHttpClient>;
  fetch: ReturnType<typeof vi.fn<FetchFunction>>;
} {
  const fetch = vi.fn<FetchFunction>(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
  );
  return { client: createHttpClient({ apiOrigin: API_ORIGIN, fetch }), fetch };
}

// requires : `fetch` a été appelé au moins une fois.
// assigns  : rien.
// ensures  : renvoie l'URL et les options du premier appel.
function firstCall(fetch: ReturnType<typeof vi.fn<FetchFunction>>): [string, RequestInit] {
  const call = fetch.mock.calls[0];
  if (call === undefined) {
    throw new Error("fetch n'a pas été appelé");
  }
  return call;
}

// requires : `promise` doit échouer.
// assigns  : rien.
// ensures  : renvoie l'`ApiError` levée ; échoue pour toute autre issue.
async function apiErrorOf(promise: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await promise.then(
    () => undefined,
    (reason: unknown) => reason,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

describe("createHttpClient", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : une requête ordinaire n'envoie ni cookie, ni jeton, ni en-tête anti-CSRF, ne suit
  //            aucune redirection et n'est jamais mise en cache.
  test("n'envoie ni cookie ni jeton par défaut", async () => {
    const { client, fetch } = clientReturning(jsonResponse(TOKENS));

    await client.request({ method: "GET", path: "/api/v1/tasks", schema: TokenResponseSchema });

    const [url, init] = firstCall(fetch);
    const headers = new Headers(init.headers);
    expect(url).toBe(`${API_ORIGIN}/api/v1/tasks`);
    expect(init.credentials).toBe("omit");
    expect(init.redirect).toBe("error");
    expect(init.cache).toBe("no-store");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(headers.has("Authorization")).toBe(false);
    expect(headers.has("X-CSRF-Protection")).toBe(false);
    expect(init.body).toBeUndefined();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : le jeton part dans `Authorization` seulement ; le corps est du JSON.
  test("envoie le jeton dans l'en-tête Authorization et le corps en JSON", async () => {
    const { client, fetch } = clientReturning(jsonResponse(TOKENS));

    await client.request({
      method: "POST",
      path: "/api/v1/tasks",
      schema: TokenResponseSchema,
      body: { title: "Courses" },
      accessToken: TOKEN,
    });

    const [url, init] = firstCall(fetch);
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.body).toBe('{"title":"Courses"}');
    expect(url).not.toContain(TOKEN);
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : seules les routes du cookie de session envoient le cookie et l'en-tête anti-CSRF.
  test("joint le cookie et l'en-tête anti-CSRF aux routes de session", async () => {
    const { client, fetch } = clientReturning(jsonResponse(TOKENS));

    await client.request({
      method: "POST",
      path: "/api/v1/auth/refresh",
      schema: TokenResponseSchema,
      withSessionCookie: true,
    });

    const [, init] = firstCall(fetch);
    expect(init.credentials).toBe("include");
    expect(new Headers(init.headers).get("X-CSRF-Protection")).toBe("1");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : un chemin qui sortirait de l'API ou de `/api/v1/` est refusé avant tout envoi.
  test.each([
    "https://attaquant.exemple.org/api/v1/tasks",
    "//attaquant.exemple.org/api/v1/tasks",
    "/api/v1/../../admin",
    "/api/v1/tasks#fragment",
    "/api/v1\\tasks",
    "/health/live",
    "api/v1/tasks",
  ])("refuse le chemin %s", async (path) => {
    const { client, fetch } = clientReturning(jsonResponse(TOKENS));

    const error = await apiErrorOf(
      client.request({ method: "GET", path, schema: TokenResponseSchema, accessToken: TOKEN }),
    );

    expect(error.kind).toBe("unexpected");
    expect(fetch).not.toHaveBeenCalled();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une réponse qui ne respecte pas son schéma est rejetée, même avec un code 200.
  test.each([
    [{ ...TOKENS, refresh_token: "fuite" }],
    [{ ...TOKENS, access_token: "<script>" }],
    [{ ...TOKENS, expires_in: -1 }],
    [[]],
  ])("rejette une réponse non conforme %#", async (body) => {
    const { client } = clientReturning(jsonResponse(body));

    const error = await apiErrorOf(
      client.request({ method: "GET", path: "/api/v1/tasks", schema: TokenResponseSchema }),
    );

    expect(error.kind).toBe("invalid_response");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : un corps qui n'est pas du JSON est rejeté.
  test.each([
    new Response("<html></html>", { status: 200, headers: { "content-type": "text/html" } }),
    new Response("{non json", { status: 200, headers: { "content-type": "application/json" } }),
    new Response("x".repeat(1_000_001), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  ])("rejette un corps illisible %#", async (response) => {
    const { client } = clientReturning(response);

    const error = await apiErrorOf(
      client.request({ method: "GET", path: "/api/v1/tasks", schema: TokenResponseSchema }),
    );

    expect(error.kind).toBe("invalid_response");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une réponse `204` sans corps est acceptée par `z.undefined()`.
  test("accepte une réponse 204", async () => {
    const { client } = clientReturning(new Response(null, { status: 204 }));

    await expect(
      client.request({ method: "POST", path: "/api/v1/auth/logout", schema: z.undefined() }),
    ).resolves.toBeUndefined();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une erreur garde son code, son délai et son identifiant, mais jamais le détail
  //            textuel de l'API, remplacé par un message générique.
  test("traduit une erreur RFC 9457 sans en reprendre le détail", async () => {
    const problem = {
      type: "about:blank",
      title: "Too Many Requests",
      status: 429,
      detail: "Détail interne à ne pas afficher",
      request_id: "abc123",
    };
    const { client } = clientReturning(jsonResponse(problem, 429, { "retry-after": "42" }));

    const error = await apiErrorOf(
      client.request({ method: "GET", path: "/api/v1/tasks", schema: TokenResponseSchema }),
    );

    expect(error.kind).toBe("rate_limited");
    expect(error.status).toBe(429);
    expect(error.retryAfterSeconds).toBe(42);
    expect(error.requestId).toBe("abc123");
    expect(error.message).not.toContain("interne");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une erreur au corps illisible reste définie par son code.
  test("garde le code d'une erreur au corps illisible", async () => {
    const { client } = clientReturning(
      new Response("panne", { status: 503, headers: { "content-type": "text/plain" } }),
    );

    const error = await apiErrorOf(
      client.request({ method: "GET", path: "/api/v1/tasks", schema: TokenResponseSchema }),
    );

    expect(error.kind).toBe("unavailable");
    expect(error.requestId).toBeUndefined();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une panne réseau devient une erreur `network`.
  test("signale une panne réseau", async () => {
    const { client } = clientReturning(new TypeError("Failed to fetch"));

    const error = await apiErrorOf(
      client.request({ method: "GET", path: "/api/v1/tasks", schema: TokenResponseSchema }),
    );

    expect(error.kind).toBe("network");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une requête trop longue est interrompue et devient une erreur `timeout`.
  test("interrompt une requête trop longue", async () => {
    // requires : `init.signal` est fourni par le client.
    // assigns  : rien.
    // ensures  : ne répond jamais, sauf par un rejet quand le signal est interrompu.
    const fetch = vi.fn<FetchFunction>(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => {
            reject(new DOMException("interrompu", "AbortError"));
          });
        }),
    );
    const client = createHttpClient({ apiOrigin: API_ORIGIN, fetch, timeoutMs: 10 });

    const error = await apiErrorOf(
      client.request({ method: "GET", path: "/api/v1/tasks", schema: TokenResponseSchema }),
    );

    expect(error.kind).toBe("timeout");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une annulation voulue par l'appelant est relayée telle quelle, pas déguisée.
  test("relaie l'annulation demandée par l'appelant", async () => {
    const controller = new AbortController();
    const abort = new DOMException("annulé", "AbortError");
    const { client } = clientReturning(abort);
    controller.abort();

    await expect(
      client.request({
        method: "GET",
        path: "/api/v1/tasks",
        schema: TokenResponseSchema,
        signal: controller.signal,
      }),
    ).rejects.toBe(abort);
  });
});
