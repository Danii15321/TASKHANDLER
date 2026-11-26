import { NextRequest } from "next/server";
import { afterEach, describe, expect, test, vi } from "vitest";

const API_ORIGIN = "https://api.taskhandler.localhost:8443";

// requires : rien.
// assigns  : variables d'environnement du test (rétablies par `afterEach`) et cache des modules.
// ensures  : renvoie le module proxy chargé avec une configuration de production valide.
async function loadProxyModule(): Promise<typeof import("@/proxy")> {
  // Le proxy lit sa configuration au chargement : on fixe l'environnement, puis on le recharge.
  vi.resetModules();
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", API_ORIGIN);
  return import("@/proxy");
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("proxy", () => {
  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : la réponse et la requête transmise au rendu portent la même CSP, celle du serveur,
  //            même si le client en envoie une autre.
  test("pose la même CSP sur la réponse et sur la requête transmise au rendu", async () => {
    const { proxy } = await loadProxyModule();
    const request = new NextRequest("https://app.taskhandler.localhost/", {
      headers: { "Content-Security-Policy": "script-src 'nonce-ATTAQUANT'" },
    });

    const response = proxy(request);
    const policy = response.headers.get("Content-Security-Policy");

    expect(policy).toMatch(/script-src 'nonce-[A-Za-z0-9+/]{22}==' 'strict-dynamic'/);
    expect(policy).toContain(`connect-src 'self' ${API_ORIGIN}`);
    // Next.js transmet les en-têtes de requête modifiés au rendu sous ce préfixe.
    expect(response.headers.get("x-middleware-request-content-security-policy")).toBe(policy);
    expect(policy).not.toContain("ATTAQUANT");
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : deux requêtes reçoivent deux nonces différents.
  test("génère un nonce neuf à chaque requête", async () => {
    const { proxy } = await loadProxyModule();
    const nonceOf = (): string | undefined =>
      proxy(new NextRequest("https://app.taskhandler.localhost/"))
        .headers.get("Content-Security-Policy")
        ?.match(/'nonce-([^']+)'/)?.[1];

    expect(nonceOf()).not.toBe(nonceOf());
  });

  // requires : rien.
  // assigns  : rien hors du test.
  // ensures  : le proxy s'exécute sur les pages et les préchargements, pas sur les fichiers
  //            statiques.
  test("couvre toutes les pages, préchargements compris, mais pas les fichiers statiques", async () => {
    const { config } = await loadProxyModule();
    // Chaque entrée est un simple motif de chemin, sans condition `has` ni `missing` : aucun
    // en-tête de requête (comme `next-router-prefetch`) ne peut donc écarter le proxy.
    expect(config.matcher.every((entry) => typeof entry === "string")).toBe(true);
    // Next.js interprète le motif comme une expression régulière ancrée sur tout le chemin.
    // (L'utilitaire `unstable_doesMiddlewareMatch` n'est pas utilisé : ses déclarations de types
    // sont cassées en 16.4.6, ce que révèle `skipLibCheck: false`.)
    const patterns = config.matcher.map((source) => new RegExp(`^${source}$`));
    const matches = (path: string): boolean => patterns.some((pattern) => pattern.test(path));

    expect(matches("/")).toBe(true);
    expect(matches("/n-existe-pas")).toBe(true);
    expect(matches("/_next/static/chunks/main.js")).toBe(false);
    expect(matches("/_next/image")).toBe(false);
    expect(matches("/favicon.ico")).toBe(false);
  });
});
