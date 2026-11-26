import { describe, expect, test } from "vitest";

import {
  assertOnlyAllowedPublicVariables,
  parsePublicConfig,
  PublicConfigError,
} from "@/config/public-config";

const PRODUCTION = { allowInsecureHttp: false } as const;
const DEVELOPMENT = { allowInsecureHttp: true } as const;

describe("parsePublicConfig", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : une origine HTTPS valide est acceptée et réduite à son origine.
  test("accepte une origine HTTPS", () => {
    const config = parsePublicConfig(
      { apiBaseUrl: " https://api.taskhandler.localhost:8443/ " },
      PRODUCTION,
    );

    expect(config).toEqual({ apiOrigin: "https://api.taskhandler.localhost:8443" });
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : HTTP n'est accepté qu'en développement local.
  test("refuse HTTP hors développement et l'accepte en développement", () => {
    const raw = { apiBaseUrl: "http://127.0.0.1:8000" };

    expect(() => parsePublicConfig(raw, PRODUCTION)).toThrow(/https hors développement/);
    expect(parsePublicConfig(raw, DEVELOPMENT)).toEqual({ apiOrigin: "http://127.0.0.1:8000" });
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : chaque valeur dangereuse ou incohérente empêche le démarrage.
  test.each([
    ["absente", undefined],
    ["vide", "   "],
    ["pas une URL", "api.taskhandler"],
    // eslint-disable-next-line no-script-url -- valeur d'attaque testée : elle doit être refusée.
    ["schéma interdit", "javascript:alert(1)"],
    ["schéma ftp", "ftp://api.taskhandler.localhost"],
    ["identifiants", "https://admin:secret@api.taskhandler.localhost"],
    ["chemin", "https://api.taskhandler.localhost/api/v1"],
    ["requête", "https://api.taskhandler.localhost/?debug=1"],
    ["fragment", "https://api.taskhandler.localhost/#token"],
  ])("refuse une adresse %s", (_label, apiBaseUrl) => {
    expect(() => parsePublicConfig({ apiBaseUrl }, PRODUCTION)).toThrow(PublicConfigError);
  });
});

describe("assertOnlyAllowedPublicVariables", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : les variables autorisées et non publiques passent le contrôle.
  test("accepte la liste fermée et les variables non publiques", () => {
    expect(() => {
      assertOnlyAllowedPublicVariables({
        NEXT_PUBLIC_API_BASE_URL: "https://api.taskhandler.localhost",
        DATABASE_URL: "sans importance ici",
      });
    }).not.toThrow();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une variable publique inconnue est refusée, et sa valeur n'apparaît jamais dans
  //            le message d'erreur.
  test("refuse une variable publique inconnue sans révéler sa valeur", () => {
    const secretValue = "valeur-secrete-a-ne-jamais-afficher";

    expect(() => {
      assertOnlyAllowedPublicVariables({ NEXT_PUBLIC_STRIPE_SECRET: secretValue });
    }).toThrow(/NEXT_PUBLIC_STRIPE_SECRET/);
    try {
      assertOnlyAllowedPublicVariables({ NEXT_PUBLIC_STRIPE_SECRET: secretValue });
    } catch (error) {
      expect(String(error)).not.toContain(secretValue);
    }
  });
});
