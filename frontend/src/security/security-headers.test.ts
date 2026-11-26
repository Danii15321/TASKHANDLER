import { describe, expect, test } from "vitest";

import { buildSecurityHeaders } from "@/security/security-headers";

// requires : `isProduction` choisit le mode du build.
// assigns  : rien.
// ensures  : renvoie les en-têtes sous forme d'objet nom → valeur.
function headersFor(isProduction: boolean): Record<string, string> {
  return Object.fromEntries(
    buildSecurityHeaders({ isProduction }).map(({ key, value }) => [key, value]),
  );
}

describe("buildSecurityHeaders", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : chaque en-tête de sécurité attendu est présent avec sa valeur stricte.
  test("pose les en-têtes de sécurité attendus", () => {
    const headers = headersFor(false);

    expect(headers["X-Content-Type-Options"]).toBe("nosniff");
    expect(headers["Referrer-Policy"]).toBe("no-referrer");
    expect(headers["X-Frame-Options"]).toBe("DENY");
    expect(headers["Cross-Origin-Opener-Policy"]).toBe("same-origin");
    expect(headers["Cross-Origin-Resource-Policy"]).toBe("same-origin");
    expect(headers["Permissions-Policy"]).toContain("camera=()");
    expect(headers["Permissions-Policy"]).toContain("geolocation=()");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : HSTS n'est envoyé qu'en production, avec la même durée que l'API.
  test("n'envoie HSTS qu'en production", () => {
    expect(headersFor(false)["Strict-Transport-Security"]).toBeUndefined();
    expect(headersFor(true)["Strict-Transport-Security"]).toBe(
      "max-age=63072000; includeSubDomains",
    );
  });
});
