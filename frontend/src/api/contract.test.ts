// Vérifie que les schémas zod du frontend correspondent au contrat OpenAPI de l'API.
//
// `openapi.json` est exporté par le backend, qui vérifie lui-même que cette copie est à jour. Ce
// test ferme la boucle : un champ ajouté, retiré, renommé ou retypé d'un côté fait échouer la CI.
import { describe, expect, test } from "vitest";
import { z } from "zod";

import contract from "@/api/openapi.json";
import { LoginRequestSchema, ProblemSchema, TokenResponseSchema } from "@/api/schemas";

// Fragment de JSON Schema, tel que l'écrivent Pydantic et zod.
interface JsonSchema {
  readonly type?: string | readonly string[];
  readonly const?: unknown;
  readonly properties?: Readonly<Record<string, JsonSchema>>;
  readonly required?: readonly string[];
  readonly items?: JsonSchema;
  readonly anyOf?: readonly JsonSchema[];
  readonly additionalProperties?: boolean | JsonSchema;
}

const components = contract.components.schemas as unknown as Readonly<Record<string, JsonSchema>>;

// requires : `schema` est un fragment de JSON Schema.
// assigns  : rien.
// ensures  : renvoie les types JSON acceptés, triés (`anyOf` et listes de types aplatis).
function typesOf(schema: JsonSchema): string[] {
  const own = schema.type === undefined ? [] : [schema.type].flat();
  const nested = (schema.anyOf ?? []).flatMap((option) => typesOf(option));
  return [...new Set([...own, ...nested])].sort();
}

// requires : `frontend` vient de `z.toJSONSchema`, `api` du contrat ; `path` situe la comparaison.
// assigns  : rien.
// ensures  : échoue si les deux schémas diffèrent par leurs champs, leurs champs obligatoires,
//            leurs types, leurs constantes ou leur refus des champs inconnus, à toute profondeur.
//            Les bornes (longueurs, intervalles) peuvent être plus strictes côté frontend.
function expectCompatible(frontend: JsonSchema, api: JsonSchema, path: string): void {
  expect(typesOf(frontend), `${path} : type`).toEqual(typesOf(api));
  expect(frontend.const, `${path} : constante`).toEqual(api.const);
  if (api.properties !== undefined || frontend.properties !== undefined) {
    const frontendProperties = frontend.properties ?? {};
    const apiProperties = api.properties ?? {};
    expect(Object.keys(frontendProperties).sort(), `${path} : champs`).toEqual(
      Object.keys(apiProperties).sort(),
    );
    expect([...(frontend.required ?? [])].sort(), `${path} : champs obligatoires`).toEqual(
      [...(api.required ?? [])].sort(),
    );
    expect(frontend.additionalProperties === false, `${path} : champs inconnus refusés`).toBe(
      api.additionalProperties === false,
    );
    for (const [name, apiProperty] of Object.entries(apiProperties)) {
      const frontendProperty = frontendProperties[name];
      if (frontendProperty !== undefined) {
        expectCompatible(frontendProperty, apiProperty, `${path}.${name}`);
      }
    }
  }
  if (api.items !== undefined) {
    expect(frontend.items, `${path} : éléments`).toBeDefined();
    expectCompatible(frontend.items ?? {}, api.items, `${path}[]`);
  }
}

// requires : `schema` est un schéma zod.
// assigns  : rien.
// ensures  : renvoie son JSON Schema, côté valeur reçue (`input`) ou produite (`output`).
function jsonSchemaOf(schema: z.ZodType, io: "input" | "output"): JsonSchema {
  return z.toJSONSchema(schema, { io, unrepresentable: "any" }) as JsonSchema;
}

describe("contrat OpenAPI", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : chaque schéma du frontend correspond au composant du même nom dans le contrat.
  test.each([
    ["LoginRequest", LoginRequestSchema, "input"],
    ["TokenResponse", TokenResponseSchema, "output"],
    ["Problem", ProblemSchema, "output"],
  ] as const)("%s correspond au contrat", (name, schema, io) => {
    const api = components[name];

    expect(api, `composant ${name} absent du contrat`).toBeDefined();
    expectCompatible(jsonSchemaOf(schema, io), api ?? {}, name);
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : le jeton de renouvellement n'apparaît dans aucun schéma du contrat.
  test("aucun schéma ne transporte le jeton de renouvellement", () => {
    expect(JSON.stringify(components)).not.toContain("refresh_token");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : le comparateur détecte bien un écart : sans cela, le test ci-dessus ne prouverait
  //            rien.
  test("le comparateur détecte un champ manquant", () => {
    const truncated = TokenResponseSchema.omit({ expires_in: true });
    const api = components["TokenResponse"] ?? {};

    expect(() => {
      expectCompatible(jsonSchemaOf(truncated, "output"), api, "TokenResponse");
    }).toThrow();
  });
});
