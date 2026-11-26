// Gestionnaire de session du navigateur.
//
// - Le jeton d'accès vit dans une variable de ce module, en mémoire, et nulle part ailleurs : ni
//   stockage du navigateur, ni cookie lisible, ni URL. Il disparaît à la fermeture de l'onglet.
// - Le jeton de renouvellement reste dans le cookie `HttpOnly` posé par l'API : ce code ne le
//   voit jamais. Recharger la page restaure la session par un renouvellement.
// - Le renouvellement est programmé avant l'expiration du jeton d'accès, passe par un verrou
//   partagé entre onglets, et n'a lieu qu'une fois à la fois dans un même onglet.
// - Connexion et déconnexion sont annoncées aux autres onglets ; aucun message ne contient de
//   jeton.
import { z } from "zod";

import { ApiError, type ApiErrorKind } from "@/api/errors";
import type { ApiRequest, HttpClient } from "@/api/http-client";
import {
  API_PREFIX,
  LoginRequestSchema,
  TokenResponseSchema,
  type LoginRequest,
  type TokenResponse,
} from "@/api/schemas";
import type { LockManagerLike } from "@/session/locks";

// Nom du verrou partagé par tous les onglets de l'application.
export const REFRESH_LOCK_NAME = "taskhandler:session-refresh";
// Nom du canal par lequel les onglets s'annoncent connexion et déconnexion.
export const SESSION_CHANNEL_NAME = "taskhandler:session";

// Le renouvellement part une minute avant l'expiration : assez tôt pour absorber un réseau lent.
const REFRESH_MARGIN_MS = 60_000;
// Jamais moins de cinq secondes entre deux renouvellements programmés.
const MIN_REFRESH_DELAY_MS = 5_000;
// Après un échec passager (réseau, serveur indisponible), nouvel essai trente secondes plus tard.
const RETRY_DELAY_MS = 30_000;
// Erreurs passagères : la session n'est pas perdue, le renouvellement sera retenté.
const TRANSIENT_KINDS: ReadonlySet<ApiErrorKind> = new Set([
  "network",
  "timeout",
  "unavailable",
  "rate_limited",
  "unexpected",
]);

// `unknown` : pas encore vérifiée ; `unreachable` : l'API n'a pas pu répondre, nouvel essai prévu.
export type SessionStatus = "unknown" | "authenticated" | "anonymous" | "unreachable";

// État public de la session. Il ne contient jamais le jeton.
export interface SessionSnapshot {
  readonly status: SessionStatus;
}

// Messages échangés entre onglets, validés à la réception comme toute donnée extérieure.
const SessionMessageSchema = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("login") }),
  z.strictObject({ type: z.literal("logout") }),
]);
export type SessionMessage = z.infer<typeof SessionMessageSchema>;

// Canal entre onglets (un `BroadcastChannel` dans le navigateur).
export interface SessionChannel {
  postMessage(message: SessionMessage): void;
  // Renvoie la fonction qui retire l'écouteur.
  onMessage(listener: (data: unknown) => void): () => void;
}

// Programme `callback` dans `delayMs` millisecondes ; renvoie la fonction d'annulation.
export type Scheduler = (callback: () => void, delayMs: number) => () => void;

// Requête authentifiée : le jeton et le cookie sont gérés par le gestionnaire, pas par l'appelant.
export type AuthenticatedRequest<T> = Omit<ApiRequest<T>, "accessToken" | "withSessionCookie">;

// Opérations offertes à l'application.
export interface SessionManager {
  getSnapshot(): SessionSnapshot;
  subscribe(listener: () => void): () => void;
  restore(): Promise<void>;
  login(credentials: LoginRequest): Promise<void>;
  logout(): Promise<void>;
  logoutAll(): Promise<void>;
  request<T>(request: AuthenticatedRequest<T>): Promise<T>;
  dispose(): void;
}

// requires : `deps.locks` est partagé entre onglets (Web Locks) ; `deps.channel` relie les
//            onglets de la même origine ; `deps.schedule` programme un rappel.
// assigns  : écoute le canal entre onglets.
// ensures  : renvoie un gestionnaire dont le jeton d'accès ne quitte jamais la mémoire de ce
//            module, et dont les renouvellements ne se chevauchent jamais, ni dans l'onglet ni
//            entre onglets.
export function createSessionManager(deps: {
  readonly http: HttpClient;
  readonly locks: LockManagerLike;
  readonly channel: SessionChannel;
  readonly schedule: Scheduler;
}): SessionManager {
  let accessToken: string | undefined;
  let snapshot: SessionSnapshot = { status: "unknown" };
  const listeners = new Set<() => void>();
  let cancelTimer: (() => void) | undefined;
  let refreshing: Promise<boolean> | undefined;
  // Incrémenté à chaque déconnexion : un renouvellement parti avant est ignoré à son retour,
  // pour qu'une déconnexion ne soit jamais annulée par une réponse tardive.
  let generation = 0;

  // requires : rien.
  // assigns  : rien.
  // ensures  : renvoie le jeton d'accès actuel. Une fonction plutôt qu'une lecture directe :
  //            après un `await`, TypeScript garderait sinon une déduction de type périmée.
  function currentToken(): string | undefined {
    return accessToken;
  }

  // requires : rien.
  // assigns  : `snapshot` ; prévient les abonnés.
  // ensures  : publie le nouvel état s'il diffère de l'actuel.
  function setStatus(status: SessionStatus): void {
    if (snapshot.status !== status) {
      snapshot = { status };
      for (const listener of listeners) {
        listener();
      }
    }
  }

  // requires : rien.
  // assigns  : `cancelTimer`.
  // ensures  : annule le renouvellement programmé, s'il y en a un.
  function clearTimer(): void {
    cancelTimer?.();
    cancelTimer = undefined;
  }

  // requires : rien.
  // assigns  : `accessToken`, `cancelTimer`, `snapshot`.
  // ensures  : oublie le jeton, annule tout renouvellement programmé et publie `status`.
  function forget(status: SessionStatus): void {
    accessToken = undefined;
    clearTimer();
    setStatus(status);
  }

  // requires : `tokens` a été validé par `TokenResponseSchema`.
  // assigns  : `accessToken`, `cancelTimer`, `snapshot`.
  // ensures  : garde le jeton en mémoire et programme son renouvellement avant expiration.
  function accept(tokens: TokenResponse): void {
    accessToken = tokens.access_token;
    setStatus("authenticated");
    clearTimer();
    const delay = Math.max(tokens.expires_in * 1000 - REFRESH_MARGIN_MS, MIN_REFRESH_DELAY_MS);
    cancelTimer = deps.schedule(() => void refresh(), delay);
  }

  // requires : rien.
  // assigns  : voir `accept` et `forget` ; programme un nouvel essai après un échec passager.
  // ensures  : renouvelle la session au plus une fois à la fois dans cet onglet, sous le verrou
  //            partagé entre onglets ; renvoie vrai si un nouveau jeton est en mémoire.
  function refresh(): Promise<boolean> {
    refreshing ??= (async (): Promise<boolean> => {
      const startedAt = generation;
      try {
        const tokens = await deps.locks.request(REFRESH_LOCK_NAME, () =>
          deps.http.request({
            method: "POST",
            path: `${API_PREFIX}/auth/refresh`,
            schema: TokenResponseSchema,
            withSessionCookie: true,
          }),
        );
        if (generation !== startedAt) {
          return false;
        }
        accept(tokens);
        return true;
      } catch (error) {
        if (generation !== startedAt) {
          return false;
        }
        const kind = error instanceof ApiError ? error.kind : "unexpected";
        if (!TRANSIENT_KINDS.has(kind)) {
          // Cookie absent, expiré ou révoqué : il n'y a plus de session.
          forget("anonymous");
          return false;
        }
        if (accessToken === undefined) {
          setStatus("unreachable");
        }
        clearTimer();
        const retryAfterMs =
          error instanceof ApiError && error.retryAfterSeconds !== undefined
            ? error.retryAfterSeconds * 1000
            : RETRY_DELAY_MS;
        cancelTimer = deps.schedule(() => void refresh(), Math.max(retryAfterMs, RETRY_DELAY_MS));
        return false;
      } finally {
        refreshing = undefined;
      }
    })();
    return refreshing;
  }

  // requires : `path` est la route de déconnexion voulue.
  // assigns  : oublie la session de cet onglet, prévient les autres, appelle l'API.
  // ensures  : l'état local est effacé même si l'API ne répond pas ; l'erreur éventuelle est
  //            alors relancée pour que l'interface signale une déconnexion incomplète.
  async function closeSession(path: string): Promise<void> {
    // Sans jeton en mémoire (onglet tout juste rechargé), le cookie peut encore ouvrir la
    // session : on renouvelle d'abord pour pouvoir prouver la demande de déconnexion à l'API.
    const token = currentToken() ?? ((await refresh()) ? currentToken() : undefined);
    generation += 1;
    forget("anonymous");
    deps.channel.postMessage({ type: "logout" });
    if (token !== undefined) {
      await deps.http.request({
        method: "POST",
        path,
        schema: z.undefined(),
        accessToken: token,
        // Le cookie doit être joint pour que la réponse puisse l'effacer.
        withSessionCookie: true,
      });
    }
  }

  // requires : `data` est un message reçu d'un autre onglet, non encore validé.
  // assigns  : voir `forget` et `refresh`.
  // ensures  : une déconnexion annoncée efface la session de cet onglet ; une connexion
  //            annoncée la restaure ; tout autre message est ignoré.
  function onChannelMessage(data: unknown): void {
    const message = SessionMessageSchema.safeParse(data);
    if (!message.success) {
      return;
    }
    if (message.data.type === "logout") {
      generation += 1;
      forget("anonymous");
    } else if (snapshot.status !== "authenticated") {
      void refresh();
    }
  }

  const stopListening = deps.channel.onMessage(onChannelMessage);

  return {
    // requires : rien.
    // assigns  : rien.
    // ensures  : renvoie l'état courant, objet stable tant qu'il ne change pas.
    getSnapshot(): SessionSnapshot {
      return snapshot;
    },

    // requires : `listener` est une fonction sans argument.
    // assigns  : ajoute `listener` aux abonnés.
    // ensures  : `listener` est appelé à chaque changement d'état ; renvoie le désabonnement.
    subscribe(listener: () => void): () => void {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    // requires : rien.
    // assigns  : voir `refresh`.
    // ensures  : restaure la session depuis le cookie, sauf si elle est déjà active.
    async restore(): Promise<void> {
      if (snapshot.status !== "authenticated") {
        await refresh();
      }
    },

    // requires : `credentials` vient d'un formulaire de connexion.
    // assigns  : ouvre une session ; prévient les autres onglets.
    // ensures  : garde le jeton d'accès en mémoire ; lève `ApiError` en cas d'échec, sans
    //            jamais conserver le mot de passe.
    async login(credentials: LoginRequest): Promise<void> {
      const body = LoginRequestSchema.safeParse(credentials);
      if (!body.success) {
        throw new ApiError("validation");
      }
      const tokens = await deps.http.request({
        method: "POST",
        path: `${API_PREFIX}/auth/login`,
        schema: TokenResponseSchema,
        body: body.data,
        withSessionCookie: true,
      });
      accept(tokens);
      deps.channel.postMessage({ type: "login" });
    },

    // requires : rien.
    // assigns  : voir `closeSession`.
    // ensures  : ferme la session de cet appareil.
    logout(): Promise<void> {
      return closeSession(`${API_PREFIX}/auth/logout`);
    },

    // requires : rien.
    // assigns  : voir `closeSession`.
    // ensures  : ferme toutes les sessions du compte, sur tous les appareils.
    logoutAll(): Promise<void> {
      return closeSession(`${API_PREFIX}/auth/logout-all`);
    },

    // requires : `request` décrit une route authentifiée de l'API.
    // assigns  : peut renouveler la session.
    // ensures  : envoie la requête avec le jeton d'accès ; sur un `401`, renouvelle une seule
    //            fois puis réessaie une seule fois ; lève `ApiError("unauthorized")` si la
    //            session est perdue.
    async request<T>(request: AuthenticatedRequest<T>): Promise<T> {
      const firstToken = currentToken() ?? ((await refresh()) ? currentToken() : undefined);
      if (firstToken === undefined) {
        throw new ApiError("unauthorized");
      }
      try {
        return await deps.http.request({ ...request, accessToken: firstToken });
      } catch (error) {
        if (!(error instanceof ApiError) || error.kind !== "unauthorized") {
          throw error;
        }
        const secondToken = (await refresh()) ? currentToken() : undefined;
        if (secondToken === undefined) {
          throw error;
        }
        return await deps.http.request({ ...request, accessToken: secondToken });
      }
    },

    // requires : rien.
    // assigns  : annule le renouvellement programmé, cesse d'écouter le canal et les abonnés.
    // ensures  : le gestionnaire n'agit plus de lui-même.
    dispose(): void {
      clearTimer();
      stopListening();
      listeners.clear();
    },
  };
}
