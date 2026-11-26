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

// Limites des mots de passe, identiques à l'API (`password_policy.py`).
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
// Longueur maximale d'une adresse e-mail (RFC 5321), identique à l'API.
export const EMAIL_MAX_LENGTH = 320;

// Format exact d'un jeton à usage unique reçu par e-mail : `secrets.token_urlsafe(32)`.
export const ONE_TIME_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const OneTimeTokenSchema = z.string().regex(ONE_TIME_TOKEN_PATTERN);

// Corps de `POST /auth/register`. La politique complète (mots de passe courants, ressemblance
// avec l'adresse) reste vérifiée par l'API, seule à faire foi.
export const RegisterRequestSchema = z.strictObject({
  email: z.string().min(1).max(EMAIL_MAX_LENGTH),
  password: z.string().max(PASSWORD_MAX_LENGTH),
});
export type RegisterRequest = z.infer<typeof RegisterRequestSchema>;

// Réponse uniforme `202` de l'inscription et du mot de passe oublié, adresse connue ou non.
export const AcceptedResponseSchema = z.strictObject({ status: z.literal("accepted") });

// Corps de `POST /auth/verify-email`.
export const VerifyEmailRequestSchema = z.strictObject({ token: OneTimeTokenSchema });

// Corps de `POST /auth/password/forgot`.
export const ForgotPasswordRequestSchema = z.strictObject({
  email: z.string().min(1).max(EMAIL_MAX_LENGTH),
});

// Corps de `POST /auth/password/reset`.
export const ResetPasswordRequestSchema = z.strictObject({
  token: OneTimeTokenSchema,
  new_password: z.string().max(PASSWORD_MAX_LENGTH),
});

// Corps de `POST /auth/login`, aux limites de l'API.
export const LoginRequestSchema = z.strictObject({
  email: z.string().min(1).max(EMAIL_MAX_LENGTH),
  password: z.string().min(1).max(PASSWORD_MAX_LENGTH),
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
