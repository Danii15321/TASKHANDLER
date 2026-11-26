// Client HTTP unique vers l'API.
//
// Tout appel à l'API passe par ici, pour que chaque règle de sécurité soit appliquée une seule
// fois : destination limitée à l'API, jeton uniquement dans `Authorization`, cookie de session
// envoyé seulement aux routes qui l'exigent, délai maximal, et validation de chaque réponse.
import type { z } from "zod";

import { ApiError, kindForStatus, parseRetryAfter } from "@/api/errors";
import { API_PREFIX, ProblemSchema } from "@/api/schemas";

// En-tête anti-CSRF exigé par l'API sur les routes qui posent ou lisent le cookie de session.
export const CSRF_HEADER = { name: "X-CSRF-Protection", value: "1" } as const;

// Délai maximal d'une requête : au-delà, l'utilisateur reçoit une erreur claire plutôt qu'une
// attente sans fin.
const DEFAULT_TIMEOUT_MS = 10_000;
// Taille maximale d'un corps de réponse lu : une réponse démesurée est refusée sans être analysée.
const MAX_RESPONSE_CHARS = 1_000_000;

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

// Description d'une requête vers l'API.
export interface ApiRequest<T> {
  readonly method: HttpMethod;
  // Chemin sous `/api/v1/`, avec une éventuelle chaîne de requête. Jamais une URL complète.
  readonly path: string;
  // Schéma de la réponse attendue ; `z.undefined()` pour une réponse `204` sans corps.
  readonly schema: z.ZodType<T>;
  readonly body?: unknown;
  // Jeton d'accès, envoyé dans l'en-tête `Authorization` et nulle part ailleurs.
  readonly accessToken?: string;
  // Vrai pour les routes du cookie de session (connexion, renouvellement, déconnexion) : le
  // navigateur envoie et accepte alors les cookies, et l'en-tête anti-CSRF est ajouté.
  readonly withSessionCookie?: boolean;
  readonly signal?: AbortSignal;
}

// Client HTTP : une seule opération, `request`.
export interface HttpClient {
  request<T>(request: ApiRequest<T>): Promise<T>;
}

// Signature de `fetch`, injectée pour pouvoir tester le client sans réseau.
export type FetchFunction = (input: string, init: RequestInit) => Promise<Response>;

// requires : `path` est le chemin demandé par l'appelant ; `apiOrigin` une origine validée.
// assigns  : rien.
// ensures  : renvoie l'URL complète si `path` désigne bien une route de l'API, sous `/api/v1/`,
//            sans remontée de répertoire ni changement d'hôte ; lève `ApiError` sinon.
function buildUrl(path: string, apiOrigin: string): string {
  // `//hote`, `\`, `..` ou `#` pourraient faire sortir la requête de l'API, ou la dévier vers
  // une autre route que celle prévue : refusés avant même de construire l'URL.
  const suspicious = /\/\/|\\|\.\.|#/.test(path);
  if (!path.startsWith(`${API_PREFIX}/`) || suspicious) {
    throw new ApiError("unexpected");
  }
  const url = new URL(path, apiOrigin);
  if (url.origin !== apiOrigin) {
    throw new ApiError("unexpected");
  }
  return url.toString();
}

// requires : `response` est une réponse reçue de l'API.
// assigns  : consomme le corps de la réponse.
// ensures  : renvoie le JSON du corps ; `undefined` pour `204` ; lève `ApiError` si le corps
//            n'est pas du JSON, ou s'il est démesuré.
async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return undefined;
  }
  const contentType = response.headers.get("content-type") ?? "";
  if (!/^application\/(problem\+)?json\b/.test(contentType)) {
    throw new ApiError("invalid_response", { status: response.status });
  }
  const text = await response.text();
  if (text.length > MAX_RESPONSE_CHARS) {
    throw new ApiError("invalid_response", { status: response.status });
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    throw new ApiError("invalid_response", { status: response.status });
  }
}

// requires : `response` est une réponse en échec (code 4xx ou 5xx).
// assigns  : consomme le corps de la réponse.
// ensures  : renvoie l'`ApiError` de la catégorie du code, avec `Retry-After` et l'identifiant
//            de requête s'ils sont valides. Le détail textuel de l'API n'est jamais repris.
async function errorFrom(response: Response): Promise<ApiError> {
  let requestId: string | undefined;
  try {
    const problem = ProblemSchema.safeParse(await readBody(response));
    requestId = problem.success ? problem.data.request_id : undefined;
  } catch {
    // Corps illisible : l'erreur reste définie par son code HTTP seul.
  }
  const retryAfterSeconds = parseRetryAfter(response.headers.get("retry-after"));
  return new ApiError(kindForStatus(response.status), {
    status: response.status,
    ...(retryAfterSeconds === undefined ? {} : { retryAfterSeconds }),
    ...(requestId === undefined ? {} : { requestId }),
  });
}

// requires : `options.apiOrigin` vient de `loadPublicConfig()` ; `options.fetch` se comporte
//            comme `fetch`.
// assigns  : rien à la création.
// ensures  : renvoie un client dont chaque requête vise l'API seule, porte au plus le jeton
//            fourni, s'interrompt après le délai, et renvoie une valeur validée par le schéma ;
//            tout échec devient une `ApiError`, sauf l'annulation par l'appelant, relayée telle
//            quelle.
export function createHttpClient(options: {
  readonly apiOrigin: string;
  readonly fetch: FetchFunction;
  readonly timeoutMs?: number;
}): HttpClient {
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  return {
    // requires : voir `ApiRequest`.
    // assigns  : envoie une requête HTTP à l'API.
    // ensures  : voir `createHttpClient`.
    async request<T>(request: ApiRequest<T>): Promise<T> {
      const url = buildUrl(request.path, options.apiOrigin);
      const headers = new Headers({ Accept: "application/json" });
      if (request.body !== undefined) {
        headers.set("Content-Type", "application/json");
      }
      if (request.accessToken !== undefined) {
        headers.set("Authorization", `Bearer ${request.accessToken}`);
      }
      if (request.withSessionCookie === true) {
        headers.set(CSRF_HEADER.name, CSRF_HEADER.value);
      }

      const timeout = AbortSignal.timeout(timeoutMs);
      const signal =
        request.signal === undefined ? timeout : AbortSignal.any([request.signal, timeout]);

      let response: Response;
      try {
        response = await options.fetch(url, {
          method: request.method,
          headers,
          ...(request.body === undefined ? {} : { body: JSON.stringify(request.body) }),
          // Le cookie de session ne part qu'avec les routes qui l'exigent ; toute autre requête
          // n'envoie ni n'accepte aucun cookie.
          credentials: request.withSessionCookie === true ? "include" : "omit",
          // Une redirection pourrait emmener la requête, et son jeton, ailleurs : refusée.
          redirect: "error",
          // Les réponses contiennent des données personnelles : jamais mises en cache.
          cache: "no-store",
          referrerPolicy: "no-referrer",
          mode: "cors",
          signal,
        });
      } catch (error) {
        if (request.signal?.aborted === true) {
          throw error;
        }
        throw new ApiError(timeout.aborted ? "timeout" : "network");
      }

      if (!response.ok) {
        throw await errorFrom(response);
      }
      const parsed = request.schema.safeParse(await readBody(response));
      if (!parsed.success) {
        throw new ApiError("invalid_response", { status: response.status });
      }
      return parsed.data;
    },
  };
}
