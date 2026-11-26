import { describe, expect, test, vi } from "vitest";

import {
  MESSAGES,
  newPasswordError,
  submitForgotPassword,
  submitLogin,
  submitRegister,
  submitResetPassword,
  verifyEmail,
} from "@/account/actions";
import { ApiError, type ApiErrorKind } from "@/api/errors";
import type { HttpClient } from "@/api/http-client";

const EMAIL = "alice@exemple.org";
const PASSWORD = "violet ruisseau lanterne 2026";
const TOKEN = "a".repeat(43);

// requires : `fields` décrit les champs du formulaire.
// assigns  : rien.
// ensures  : renvoie le `FormData` correspondant.
function form(fields: Record<string, string>): FormData {
  const data = new FormData();
  for (const [name, value] of Object.entries(fields)) {
    data.set(name, value);
  }
  return data;
}

// requires : `outcome` est la réponse simulée de l'API, ou l'erreur qu'elle lève.
// assigns  : rien.
// ensures  : renvoie un faux client HTTP et son espion.
function fakeHttp(outcome: unknown): { http: HttpClient; request: ReturnType<typeof vi.fn> } {
  const request = vi.fn(() =>
    outcome instanceof ApiError ? Promise.reject(outcome) : Promise.resolve(outcome),
  );
  return { http: { request } as unknown as HttpClient, request };
}

describe("connexion", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : des identifiants complets ouvrent la session, adresse nettoyée des espaces.
  test("ouvre la session", async () => {
    const login = vi.fn(() => Promise.resolve());

    const state = await submitLogin({ login }, form({ email: ` ${EMAIL} `, password: PASSWORD }));

    expect(state.status).toBe("success");
    expect(login).toHaveBeenCalledWith({ email: EMAIL, password: PASSWORD });
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : des champs vides sont signalés sans appeler l'API.
  test("signale les champs vides sans appeler l'API", async () => {
    const login = vi.fn();

    const state = await submitLogin({ login }, form({}));

    expect(state).toMatchObject({
      status: "error",
      fields: { email: MESSAGES.emailRequired, password: MESSAGES.passwordRequired },
    });
    expect(login).not.toHaveBeenCalled();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : adresse inconnue, mot de passe faux ou saisie refusée : un seul et même message,
  //            et jamais le mot de passe dans l'état.
  test.each<ApiErrorKind>(["unauthorized", "validation"])(
    "un échec %s donne le message unique",
    async (kind) => {
      const login = vi.fn(() => Promise.reject(new ApiError(kind)));

      const state = await submitLogin({ login }, form({ email: EMAIL, password: PASSWORD }));

      expect(state).toEqual({
        status: "error",
        email: EMAIL,
        message: MESSAGES.invalidCredentials,
        fields: {},
      });
      expect(JSON.stringify(state)).not.toContain(PASSWORD);
    },
  );

  // requires : rien.
  // assigns  : rien.
  // ensures  : un `403` (adresse non vérifiée) donne un message prudent, et une limite atteinte
  //            indique le délai à attendre.
  test("traduit le blocage et la limite atteinte", async () => {
    const blocked = await submitLogin(
      { login: () => Promise.reject(new ApiError("forbidden")) },
      form({ email: EMAIL, password: PASSWORD }),
    );
    const limited = await submitLogin(
      { login: () => Promise.reject(new ApiError("rate_limited", { retryAfterSeconds: 42 })) },
      form({ email: EMAIL, password: PASSWORD }),
    );

    expect(blocked).toMatchObject({ message: MESSAGES.loginBlocked });
    expect(limited).toMatchObject({ message: "Trop de tentatives. Réessayez dans 42 secondes." });
  });
});

describe("inscription", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : l'inscription envoie l'adresse et le mot de passe, puis confirme.
  test("envoie la demande et confirme", async () => {
    const { http, request } = fakeHttp({ status: "accepted" });

    const state = await submitRegister(http, form({ email: EMAIL, password: PASSWORD }));

    expect(state).toMatchObject({ status: "success", message: MESSAGES.registered });
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        method: "POST",
        path: "/api/v1/auth/register",
        body: { email: EMAIL, password: PASSWORD },
      }),
    );
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : la confirmation est identique pour une adresse libre ou déjà inscrite : l'API
  //            répond `202` dans les deux cas, et l'écran n'a rien d'autre pour les distinguer.
  test("confirme à l'identique pour deux adresses", async () => {
    const first = await submitRegister(
      fakeHttp({ status: "accepted" }).http,
      form({ email: "libre@exemple.org", password: PASSWORD }),
    );
    const second = await submitRegister(
      fakeHttp({ status: "accepted" }).http,
      form({ email: "deja@exemple.org", password: PASSWORD }),
    );

    expect(first.status === "success" && first.message).toBe(
      second.status === "success" && second.message,
    );
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : une adresse invalide ou un mot de passe trop court est signalé sans appel.
  test("valide localement sans appeler l'API", async () => {
    const { http, request } = fakeHttp({ status: "accepted" });

    const state = await submitRegister(http, form({ email: "pas-une-adresse", password: "court" }));

    expect(state).toMatchObject({
      status: "error",
      fields: { email: MESSAGES.emailInvalid, password: MESSAGES.passwordTooShort },
    });
    expect(request).not.toHaveBeenCalled();
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : un refus de l'API (mot de passe courant, ressemblant à l'adresse) est expliqué
  //            sans détail technique ; une panne donne le message générique.
  test("traduit le refus et la panne", async () => {
    const refused = await submitRegister(
      fakeHttp(new ApiError("validation")).http,
      form({ email: EMAIL, password: PASSWORD }),
    );
    const down = await submitRegister(
      fakeHttp(new ApiError("unavailable")).http,
      form({ email: EMAIL, password: PASSWORD }),
    );

    expect(refused).toMatchObject({ message: MESSAGES.passwordRefused });
    expect(down).toMatchObject({ message: new ApiError("unavailable").message });
  });
});

describe("politique de mot de passe", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : seule la longueur compte, en points de code après NFKC, de 12 à 128 ; aucune règle
  //            de composition.
  test.each([
    ["a".repeat(11), MESSAGES.passwordTooShort],
    ["a".repeat(12), undefined],
    ["tout en minuscules sans chiffre", undefined],
    ["🔒".repeat(12), undefined],
    ["a".repeat(128), undefined],
    ["a".repeat(129), MESSAGES.passwordTooLong],
  ])("%s", (password, expected) => {
    expect(newPasswordError(password)).toBe(expected);
  });
});

describe("mot de passe oublié", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : la même confirmation est affichée, et une adresse invalide est signalée sans appel.
  test("confirme uniformément et valide l'adresse", async () => {
    const { http, request } = fakeHttp({ status: "accepted" });

    const sent = await submitForgotPassword(http, form({ email: EMAIL }));
    const invalid = await submitForgotPassword(http, form({ email: "" }));
    const limited = await submitForgotPassword(
      fakeHttp(new ApiError("rate_limited")).http,
      form({ email: EMAIL }),
    );

    expect(sent).toMatchObject({ status: "success", message: MESSAGES.resetRequested });
    expect(invalid).toMatchObject({ fields: { email: MESSAGES.emailRequired } });
    expect(limited).toMatchObject({ message: new ApiError("rate_limited").message });
    expect(request).toHaveBeenCalledTimes(1);
  });
});

describe("réinitialisation", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : le jeton part dans le corps avec le nouveau mot de passe.
  test("envoie le jeton dans le corps", async () => {
    const { http, request } = fakeHttp(undefined);

    const state = await submitResetPassword(http, TOKEN, form({ password: PASSWORD }));

    expect(state).toMatchObject({ status: "success", message: MESSAGES.passwordChanged });
    expect(request).toHaveBeenCalledWith(
      expect.objectContaining({
        path: "/api/v1/auth/password/reset",
        body: { token: TOKEN, new_password: PASSWORD },
      }),
    );
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : lien expiré ou déjà utilisé, mot de passe refusé, trop court ou panne : chaque cas
  //            a son message.
  test.each([
    [new ApiError("bad_request"), PASSWORD, MESSAGES.invalidLink],
    [new ApiError("validation"), PASSWORD, MESSAGES.passwordRefused],
    [new ApiError("network"), PASSWORD, new ApiError("network").message],
    [undefined, "court", MESSAGES.fixFields],
  ])("traduit l'échec %#", async (outcome, password, message) => {
    const state = await submitResetPassword(fakeHttp(outcome).http, TOKEN, form({ password }));

    expect(state).toMatchObject({ status: "error", message });
  });
});

describe("vérification d'adresse", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : un lien valide vérifie l'adresse ; un lien sans jeton, expiré ou déjà utilisé
  //            donne le message de lien invalide ; une panne le message générique.
  test.each([
    [TOKEN, undefined, "success", MESSAGES.emailVerified],
    [null, undefined, "error", MESSAGES.invalidLink],
    [TOKEN, new ApiError("bad_request"), "error", MESSAGES.invalidLink],
    [TOKEN, new ApiError("unavailable"), "error", new ApiError("unavailable").message],
  ])("jeton %s", async (token, outcome, status, message) => {
    const state = await verifyEmail(fakeHttp(outcome).http, token);

    expect(state).toMatchObject({ status, message });
  });
});
