// Schémas des échanges avec l'API.
//
// Ces schémas zod sont l'unique description de l'API côté frontend : les types TypeScript en sont
// déduits (`z.infer`), et chaque réponse est validée à l'exécution avant usage. Une réponse de
// l'API est une donnée externe, traitée comme non fiable (OWASP API10).
//
// `contract.test.ts` compare chacun de ces schémas à `openapi.json`, la copie du contrat exportée
// par le backend : un champ renommé, ajouté ou retiré d'un côté fait échouer les tests.
import { z } from "zod";

// Préfixe versionné de toutes les routes métier de l'API.
export const API_PREFIX = "/api/v1";

// Un JWT : trois segments base64url séparés par des points. Borné, pour ne jamais conserver en
// mémoire une valeur démesurée renvoyée par erreur.
const JWT_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;

// Corps de `POST /auth/login`, aux limites de l'API.
export const LoginRequestSchema = z.strictObject({
  email: z.string().min(1).max(320),
  password: z.string().min(1).max(128),
});
export type LoginRequest = z.infer<typeof LoginRequestSchema>;

// Réponse de la connexion et du renouvellement. `strictObject` : un champ inattendu (par exemple
// un jeton de renouvellement réapparu dans le corps) fait rejeter la réponse entière.
export const TokenResponseSchema = z.strictObject({
  access_token: z.string().max(4096).regex(JWT_PATTERN),
  token_type: z.literal("bearer"),
  // Quinze minutes côté API ; la borne haute protège d'une valeur aberrante.
  expires_in: z.int().positive().max(3600),
});
export type TokenResponse = z.infer<typeof TokenResponseSchema>;

// Erreur au format RFC 9457. `looseObject`, comme le contrat : la RFC autorise des champs
// d'extension. Seul `request_id` sert ; le détail brut n'est jamais affiché à l'utilisateur.
export const ProblemSchema = z.looseObject({
  type: z.string(),
  title: z.string(),
  status: z.int(),
  detail: z.string().optional(),
  request_id: z.string().max(64),
  errors: z
    .array(
      z.looseObject({
        loc: z.array(z.union([z.string(), z.int()])).optional(),
        message: z.string().optional(),
        type: z.string().optional(),
      }),
    )
    .optional(),
});
export type Problem = z.infer<typeof ProblemSchema>;
