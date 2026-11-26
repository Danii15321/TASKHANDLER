import { describe, expect, test } from "vitest";

import { ApiError, kindForStatus, parseRetryAfter, userMessage } from "@/api/errors";

describe("erreurs du client HTTP", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : chaque code HTTP connu a sa catégorie ; un code imprévu est `unexpected`.
  test.each([
    [400, "bad_request"],
    [401, "unauthorized"],
    [403, "forbidden"],
    [404, "not_found"],
    [409, "conflict"],
    [413, "validation"],
    [422, "validation"],
    [429, "rate_limited"],
    [502, "unavailable"],
    [503, "unavailable"],
    [504, "unavailable"],
    [500, "unexpected"],
    [418, "unexpected"],
  ])("le code %i devient %s", (status, kind) => {
    expect(kindForStatus(status)).toBe(kind);
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : seul un nombre entier raisonnable de secondes est retenu.
  test.each([
    ["42", 42],
    [" 0 ", 0],
    ["86400", 86_400],
    ["86401", undefined],
    ["-1", undefined],
    ["1.5", undefined],
    ["Wed, 21 Oct 2026 07:28:00 GMT", undefined],
    [null, undefined],
  ])("Retry-After %s donne %s", (value, expected) => {
    expect(parseRetryAfter(value)).toBe(expected);
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : le message affiché est générique, jamais celui d'une exception quelconque.
  test("n'affiche jamais le message d'une exception quelconque", () => {
    expect(userMessage(new ApiError("not_found"))).toBe("Élément introuvable.");
    expect(userMessage(new Error("SELECT password_hash FROM users"))).toBe(
      "Une erreur inattendue est survenue.",
    );
    expect(userMessage("chaîne levée")).toBe("Une erreur inattendue est survenue.");
  });
});
