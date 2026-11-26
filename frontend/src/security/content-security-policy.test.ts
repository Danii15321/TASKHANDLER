import { describe, expect, test } from "vitest";

import { buildContentSecurityPolicy, createNonce } from "@/security/content-security-policy";

const API_ORIGIN = "https://api.taskhandler.localhost:8443";

// requires : `policy` est une valeur d'en-tête CSP.
// assigns  : rien.
// ensures  : renvoie les sources de la directive `name`, ou `undefined` si elle est absente.
function directive(policy: string, name: string): string[] | undefined {
  const entry = policy
    .split(";")
    .map((part) => part.trim().split(/\s+/))
    .find(([directiveName]) => directiveName === name);
  return entry?.slice(1);
}

describe("createNonce", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : un nonce code 128 bits en base64 et ne se répète pas.
  test("produit 128 bits aléatoires, jamais deux fois les mêmes", () => {
    const nonces = new Set(Array.from({ length: 1000 }, () => createNonce()));

    expect(nonces.size).toBe(1000);
    for (const nonce of nonces) {
      expect(nonce).toMatch(/^[A-Za-z0-9+/]{22}==$/);
    }
  });
});

describe("buildContentSecurityPolicy", () => {
  const production = buildContentSecurityPolicy({
    nonce: "bm9uY2U=",
    apiOrigin: API_ORIGIN,
    isDevelopment: false,
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : en production, seuls les scripts portant le nonce s'exécutent.
  test("n'autorise en production que les scripts portant le nonce", () => {
    expect(directive(production, "default-src")).toEqual(["'none'"]);
    expect(directive(production, "script-src")).toEqual(["'nonce-bm9uY2U='", "'strict-dynamic'"]);
    expect(production).not.toContain("'unsafe-inline'");
    expect(production).not.toContain("'unsafe-eval'");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : les directives qui n'ont pas de repli sur `default-src` sont toutes fermées.
  test("ferme les cadres, la base, les objets et limite le réseau à l'API", () => {
    expect(directive(production, "frame-ancestors")).toEqual(["'none'"]);
    expect(directive(production, "base-uri")).toEqual(["'none'"]);
    expect(directive(production, "object-src")).toEqual(["'none'"]);
    expect(directive(production, "form-action")).toEqual(["'self'"]);
    expect(directive(production, "connect-src")).toEqual(["'self'", API_ORIGIN]);
    expect(directive(production, "upgrade-insecure-requests")).toEqual([]);
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : les assouplissements de développement n'existent que sous `next dev`.
  test("n'assouplit la politique qu'en développement", () => {
    const development = buildContentSecurityPolicy({
      nonce: "bm9uY2U=",
      apiOrigin: "http://127.0.0.1:8000",
      isDevelopment: true,
    });

    expect(directive(development, "script-src")).toContain("'unsafe-eval'");
    expect(directive(development, "upgrade-insecure-requests")).toBeUndefined();
  });
});
