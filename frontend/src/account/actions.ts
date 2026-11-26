// Logique des formulaires de compte, séparée des écrans pour être testée seule.
//
// Règles communes :
// - Aucun message ne permet de savoir si une adresse est inscrite : l'inscription et le mot de
//   passe oublié affichent la même confirmation, quelle que soit la réponse réelle.
// - La connexion n'a qu'un message d'échec, `Identifiants invalides.`, sans dire si c'est
//   l'adresse ou le mot de passe qui est faux.
// - Les contrôles locaux guident l'utilisateur ; l'API reste la seule validation qui fait foi.
// - Un mot de passe n'est jamais renvoyé dans l'état du formulaire : seule l'adresse l'est.
import { z } from "zod";

import { ApiError, userMessage } from "@/api/errors";
import type { HttpClient } from "@/api/http-client";
import {
  AcceptedResponseSchema,
  API_PREFIX,
  EMAIL_MAX_LENGTH,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
} from "@/api/schemas";
import type { SessionManager } from "@/session/session-manager";

// Erreurs rattachées à un champ, affichées à côté de lui.
export interface FieldErrors {
  readonly email?: string;
  readonly password?: string;
}

// État d'un formulaire après envoi.
export type FormState =
  | { readonly status: "idle"; readonly email: string }
  | { readonly status: "success"; readonly email: string; readonly message: string }
  | {
      readonly status: "error";
      readonly email: string;
      readonly message: string;
      readonly fields: FieldErrors;
    };

export const INITIAL_FORM_STATE: FormState = { status: "idle", email: "" };

// Messages affichés. Ceux de l'inscription et du mot de passe oublié sont volontairement
// identiques que l'adresse existe ou non.
export const MESSAGES = {
  invalidCredentials: "Identifiants invalides.",
  loginBlocked:
    "Connexion impossible pour le moment. Si votre adresse n’est pas encore vérifiée, un nouveau " +
    "lien vient de vous être envoyé.",
  registered:
    "Si cette adresse peut être utilisée, un e-mail vient d’y être envoyé. Suivez le lien qu’il " +
    "contient pour activer votre compte.",
  resetRequested:
    "Si un compte correspond à cette adresse, un e-mail de réinitialisation vient d’y être " +
    "envoyé. Le lien est valable 30 minutes.",
  passwordChanged: "Votre mot de passe a été changé. Toutes vos sessions ont été fermées.",
  emailVerified: "Votre adresse est vérifiée. Vous pouvez vous connecter.",
  invalidLink: "Ce lien est invalide, expiré ou déjà utilisé. Demandez-en un nouveau.",
  passwordRefused:
    "Ce mot de passe est refusé : choisissez-en un plus long, sans rapport avec votre adresse " +
    "et absent des listes de mots de passe courants.",
  emailRequired: "Saisissez votre adresse e-mail.",
  emailInvalid: "Saisissez une adresse e-mail valide.",
  passwordRequired: "Saisissez votre mot de passe.",
  passwordTooShort: `Le mot de passe doit contenir au moins ${String(PASSWORD_MIN_LENGTH)} caractères.`,
  passwordTooLong: `Le mot de passe doit contenir au plus ${String(PASSWORD_MAX_LENGTH)} caractères.`,
  fixFields: "Corrigez les champs signalés.",
} as const;

// requires : `formData` vient d'un formulaire ; `name` est le nom d'un champ.
// assigns  : rien.
// ensures  : renvoie la valeur texte du champ, ou `""` s'il est absent ou n'est pas du texte.
function text(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value : "";
}

// requires : `email` est la saisie brute.
// assigns  : rien.
// ensures  : renvoie le message d'erreur du champ, ou `undefined` si l'adresse est plausible.
//            Le contrôle reste volontairement simple : seule l'API valide vraiment l'adresse.
function emailError(email: string): string | undefined {
  if (email === "") {
    return MESSAGES.emailRequired;
  }
  if (email.length > EMAIL_MAX_LENGTH || !/^[^\s@]+@[^\s@]+$/.test(email)) {
    return MESSAGES.emailInvalid;
  }
  return undefined;
}

// requires : `password` est la saisie brute d'un nouveau mot de passe.
// assigns  : rien.
// ensures  : renvoie le message d'erreur si la longueur sort de 12 à 128 caractères, comptés
//            après normalisation NFKC comme le fait l'API ; `undefined` sinon. Aucune règle de
//            composition (majuscule, chiffre…) : elles n'améliorent pas la sécurité (NIST).
export function newPasswordError(password: string): string | undefined {
  // `Array.from` compte les points de code, comme `len` en Python côté API : un émoji compte
  // pour un caractère, et non pour les deux unités UTF-16 de `.length`.
  const length = Array.from(password.normalize("NFKC")).length;
  if (length < PASSWORD_MIN_LENGTH) {
    return MESSAGES.passwordTooShort;
  }
  if (length > PASSWORD_MAX_LENGTH) {
    return MESSAGES.passwordTooLong;
  }
  return undefined;
}

// requires : `email` est l'adresse saisie ; `fields` les erreurs de champ.
// assigns  : rien.
// ensures  : renvoie l'état d'erreur des champs, avec le message général correspondant.
function fieldFailure(email: string, fields: FieldErrors): FormState {
  return { status: "error", email, message: MESSAGES.fixFields, fields };
}

// requires : `error` est une valeur levée par un appel à l'API.
// assigns  : rien.
// ensures  : renvoie le message à afficher pour une erreur commune à tous les formulaires : délai
//            précis pour une limite atteinte, message générique sinon.
function commonMessage(error: unknown): string {
  if (error instanceof ApiError && error.kind === "rate_limited") {
    return error.retryAfterSeconds === undefined
      ? userMessage(error)
      : `Trop de tentatives. Réessayez dans ${String(error.retryAfterSeconds)} secondes.`;
  }
  return userMessage(error);
}

// requires : `email` est l'adresse saisie ; `message` le message à afficher.
// assigns  : rien.
// ensures  : renvoie un état d'erreur générale, sans erreur de champ.
function failure(email: string, message: string): FormState {
  return { status: "error", email, message, fields: {} };
}

// requires : `error` a été levée par un appel à l'API ; `kind` une catégorie d'erreur.
// assigns  : rien.
// ensures  : vrai si `error` est une `ApiError` de cette catégorie.
function isKind(error: unknown, kind: ApiError["kind"]): boolean {
  return error instanceof ApiError && error.kind === kind;
}

// requires : `session` est le gestionnaire de l'onglet ; `formData` vient du formulaire.
// assigns  : ouvre une session si les identifiants sont bons.
// ensures  : renvoie `success`, ou une erreur dont le message ne distingue jamais l'adresse du
//            mot de passe.
export async function submitLogin(
  session: Pick<SessionManager, "login">,
  formData: FormData,
): Promise<FormState> {
  const email = text(formData, "email").trim();
  const password = text(formData, "password");
  const fields: FieldErrors = {
    ...(email === "" ? { email: MESSAGES.emailRequired } : {}),
    ...(password === "" ? { password: MESSAGES.passwordRequired } : {}),
  };
  if (Object.keys(fields).length > 0) {
    return fieldFailure(email, fields);
  }
  try {
    await session.login({ email, password });
    return { status: "success", email, message: "" };
  } catch (error) {
    // Adresse inconnue, mot de passe faux, compte inactif ou saisie hors limites : un seul
    // message, pour ne jamais révéler quelle partie était fausse.
    if (isKind(error, "unauthorized") || isKind(error, "validation")) {
      return failure(email, MESSAGES.invalidCredentials);
    }
    if (isKind(error, "forbidden")) {
      return failure(email, MESSAGES.loginBlocked);
    }
    return failure(email, commonMessage(error));
  }
}

// requires : `http` est le client de l'API ; `formData` vient du formulaire d'inscription.
// assigns  : demande la création du compte à l'API.
// ensures  : renvoie le même `success` que l'adresse soit libre ou déjà inscrite ; une erreur
//            seulement pour une saisie refusée, une limite atteinte ou une panne.
export async function submitRegister(http: HttpClient, formData: FormData): Promise<FormState> {
  const email = text(formData, "email").trim();
  const password = text(formData, "password");
  const emailProblem = emailError(email);
  const passwordProblem = newPasswordError(password);
  const fields: FieldErrors = {
    ...(emailProblem === undefined ? {} : { email: emailProblem }),
    ...(passwordProblem === undefined ? {} : { password: passwordProblem }),
  };
  if (Object.keys(fields).length > 0) {
    return fieldFailure(email, fields);
  }
  try {
    await http.request({
      method: "POST",
      path: `${API_PREFIX}/auth/register`,
      schema: AcceptedResponseSchema,
      body: { email, password },
    });
    return { status: "success", email, message: MESSAGES.registered };
  } catch (error) {
    if (isKind(error, "validation")) {
      return failure(email, MESSAGES.passwordRefused);
    }
    return failure(email, commonMessage(error));
  }
}

// requires : `http` est le client de l'API ; `formData` vient du formulaire.
// assigns  : demande l'envoi d'un lien de réinitialisation.
// ensures  : renvoie le même `success` que l'adresse soit connue ou non.
export async function submitForgotPassword(
  http: HttpClient,
  formData: FormData,
): Promise<FormState> {
  const email = text(formData, "email").trim();
  const problem = emailError(email);
  if (problem !== undefined) {
    return fieldFailure(email, { email: problem });
  }
  try {
    await http.request({
      method: "POST",
      path: `${API_PREFIX}/auth/password/forgot`,
      schema: AcceptedResponseSchema,
      body: { email },
    });
    return { status: "success", email, message: MESSAGES.resetRequested };
  } catch (error) {
    return failure(email, commonMessage(error));
  }
}

// requires : `http` est le client de l'API ; `token` le jeton lu dans le lien ; `formData` vient
//            du formulaire.
// assigns  : demande le changement du mot de passe ; l'API coupe alors toutes les sessions.
// ensures  : renvoie `success`, ou une erreur : lien invalide (à redemander), mot de passe
//            refusé (le lien reste utilisable), limite ou panne.
export async function submitResetPassword(
  http: HttpClient,
  token: string,
  formData: FormData,
): Promise<FormState> {
  const password = text(formData, "password");
  const problem = newPasswordError(password);
  if (problem !== undefined) {
    return fieldFailure("", { password: problem });
  }
  try {
    await http.request({
      method: "POST",
      path: `${API_PREFIX}/auth/password/reset`,
      schema: z.undefined(),
      body: { token, new_password: password },
    });
    return { status: "success", email: "", message: MESSAGES.passwordChanged };
  } catch (error) {
    if (isKind(error, "bad_request")) {
      return failure("", MESSAGES.invalidLink);
    }
    if (isKind(error, "validation")) {
      return failure("", MESSAGES.passwordRefused);
    }
    return failure("", commonMessage(error));
  }
}

// requires : `http` est le client de l'API ; `token` le jeton lu dans le lien, ou `null`.
// assigns  : demande la vérification de l'adresse.
// ensures  : renvoie `success` si l'adresse est vérifiée ; une erreur « lien invalide » sans
//            jeton valide ou si l'API refuse le lien ; un message générique en cas de panne.
export async function verifyEmail(http: HttpClient, token: string | null): Promise<FormState> {
  if (token === null) {
    return failure("", MESSAGES.invalidLink);
  }
  try {
    await http.request({
      method: "POST",
      path: `${API_PREFIX}/auth/verify-email`,
      schema: z.undefined(),
      body: { token },
    });
    return { status: "success", email: "", message: MESSAGES.emailVerified };
  } catch (error) {
    if (isKind(error, "bad_request")) {
      return failure("", MESSAGES.invalidLink);
    }
    return failure("", commonMessage(error));
  }
}
