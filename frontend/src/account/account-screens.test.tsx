// @vitest-environment jsdom
// Tests des écrans de compte dans un DOM simulé : saisie, collage, envoi, messages, adresse.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { MESSAGES } from "@/account/actions";
import { ForgotPasswordForm } from "@/account/forgot-password-form";
import { resetFragmentTokenForTests } from "@/account/fragment-token";
import { LoginForm } from "@/account/login-form";
import { RegisterForm } from "@/account/register-form";
import { ResetPasswordForm } from "@/account/reset-password-form";
import { VerifyEmailStatus } from "@/account/verify-email-status";
import { ApiError } from "@/api/errors";

const { router, manager, http } = vi.hoisted(() => ({
  router: { replace: vi.fn(), push: vi.fn(), prefetch: vi.fn() },
  manager: { login: vi.fn() },
  http: { request: vi.fn() },
}));

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/session/browser-session", () => ({
  getBrowserSessionManager: () => manager,
  getBrowserHttpClient: () => http,
}));

const EMAIL = "alice@exemple.org";
const PASSWORD = "violet ruisseau lanterne 2026";
const TOKEN = "Q".repeat(43);

// requires : `ui` est un écran à tester.
// assigns  : monte l'écran dans le DOM simulé.
// ensures  : rend l'écran en mode strict, comme `next dev`, et renvoie l'utilisateur simulé.
function renderStrict(ui: ReactNode): ReturnType<typeof userEvent.setup> {
  render(<StrictMode>{ui}</StrictMode>);
  return userEvent.setup();
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  resetFragmentTokenForTests();
});

describe("connexion", () => {
  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : les champs sont libellés et annoncent leur rôle aux gestionnaires de mots de passe ;
  //            aucune longueur maximale ne tronquerait un mot de passe collé.
  test("expose des champs accessibles et compatibles avec les gestionnaires", () => {
    renderStrict(<LoginForm />);

    const email = screen.getByLabelText("Adresse e-mail");
    const password = screen.getByLabelText("Mot de passe");
    expect(email).toHaveProperty("autocomplete", "username");
    expect(password).toHaveProperty("autocomplete", "current-password");
    expect(password).toHaveProperty("type", "password");
    expect(password.hasAttribute("maxlength")).toBe(false);
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : un mot de passe collé arrive entier ; la connexion réussie mène aux tâches.
  test("accepte un mot de passe collé et redirige après connexion", async () => {
    manager.login.mockResolvedValue(undefined);
    const user = renderStrict(<LoginForm />);

    await user.type(screen.getByLabelText("Adresse e-mail"), EMAIL);
    await user.click(screen.getByLabelText("Mot de passe"));
    await user.paste(PASSWORD);
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    await waitFor(() => {
      expect(router.replace).toHaveBeenCalledWith("/tasks");
    });
    expect(manager.login).toHaveBeenCalledWith({ email: EMAIL, password: PASSWORD });
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : un échec affiche le message unique, garde l'adresse et vide le mot de passe.
  test("affiche le message unique et ne garde pas le mot de passe", async () => {
    manager.login.mockRejectedValue(new ApiError("unauthorized"));
    const user = renderStrict(<LoginForm />);

    await user.type(screen.getByLabelText("Adresse e-mail"), EMAIL);
    await user.type(screen.getByLabelText("Mot de passe"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Se connecter" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      MESSAGES.invalidCredentials,
    );
    expect(screen.getByLabelText("Adresse e-mail")).toHaveProperty("value", EMAIL);
    expect(screen.getByLabelText("Mot de passe")).toHaveProperty("value", "");
    expect(router.replace).not.toHaveBeenCalled();
  });
});

describe("inscription", () => {
  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : un mot de passe trop court est signalé sur le champ, relié par `aria-describedby`.
  test("signale un mot de passe trop court sur le champ", async () => {
    const user = renderStrict(<RegisterForm />);

    await user.type(screen.getByLabelText("Adresse e-mail"), EMAIL);
    await user.type(screen.getByLabelText("Mot de passe"), "court");
    await user.click(screen.getByRole("button", { name: "Créer mon compte" }));

    const password = await screen.findByLabelText("Mot de passe");
    await waitFor(() => {
      expect(password.getAttribute("aria-invalid")).toBe("true");
    });
    const describedBy = password.getAttribute("aria-describedby") ?? "";
    const descriptions = describedBy.split(" ").map((id) => document.getElementById(id));
    expect(descriptions.map((node) => node?.textContent)).toContain(MESSAGES.passwordTooShort);
    expect(http.request).not.toHaveBeenCalled();
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : l'écran de confirmation est strictement le même pour deux adresses différentes.
  test("affiche le même écran pour une adresse libre ou déjà prise", async () => {
    http.request.mockResolvedValue({ status: "accepted" });
    const screens: string[] = [];
    for (const email of ["libre@exemple.org", "deja@exemple.org"]) {
      const user = renderStrict(<RegisterForm />);
      await user.type(screen.getByLabelText("Adresse e-mail"), email);
      await user.type(screen.getByLabelText("Mot de passe"), PASSWORD);
      await user.click(screen.getByRole("button", { name: "Créer mon compte" }));
      await screen.findByRole("status");
      screens.push(document.body.textContent);
      cleanup();
    }

    expect(screens[0]).toBe(screens[1]);
    expect(screens[0]).toContain(MESSAGES.registered);
  });
});

describe("mot de passe oublié", () => {
  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : la même confirmation est affichée, avec un retour vers la connexion.
  test("confirme sans révéler si l'adresse existe", async () => {
    http.request.mockResolvedValue({ status: "accepted" });
    const user = renderStrict(<ForgotPasswordForm />);

    await user.type(screen.getByLabelText("Adresse e-mail"), EMAIL);
    await user.click(screen.getByRole("button", { name: "Recevoir un lien" }));

    expect(await screen.findByRole("status")).toHaveProperty(
      "textContent",
      MESSAGES.resetRequested,
    );
    expect(screen.getByRole("link", { name: "Retour à la connexion" })).toBeDefined();
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : une adresse vide est signalée sans appel.
  test("signale une adresse vide", async () => {
    const user = renderStrict(<ForgotPasswordForm />);

    await user.click(screen.getByRole("button", { name: "Recevoir un lien" }));

    expect(await screen.findByText(MESSAGES.emailRequired)).toBeDefined();
    expect(http.request).not.toHaveBeenCalled();
  });
});

describe("réinitialisation par lien", () => {
  // requires : rien.
  // assigns  : modifie l'adresse de la page simulée.
  // ensures  : le jeton disparaît de l'adresse dès l'affichage, puis part dans le corps ; après
  //            réussite, l'utilisateur est invité à se connecter.
  test("efface le jeton de l'adresse et l'envoie dans le corps", async () => {
    window.history.replaceState(null, "", `/reset-password#token=${TOKEN}`);
    http.request.mockResolvedValue(undefined);
    const user = renderStrict(<ResetPasswordForm />);

    const password = await screen.findByLabelText("Nouveau mot de passe");
    expect(window.location.href).not.toContain(TOKEN);
    expect(password).toHaveProperty("autocomplete", "new-password");

    await user.type(password, PASSWORD);
    await user.click(screen.getByRole("button", { name: "Changer mon mot de passe" }));

    expect(await screen.findByRole("status")).toHaveProperty(
      "textContent",
      MESSAGES.passwordChanged,
    );
    expect(http.request).toHaveBeenCalledWith(
      expect.objectContaining({ body: { token: TOKEN, new_password: PASSWORD } }),
    );
  });

  // requires : rien.
  // assigns  : modifie l'adresse de la page simulée.
  // ensures  : un lien expiré ou déjà utilisé mène à une nouvelle demande, sans formulaire.
  test("propose un nouveau lien quand le jeton est refusé", async () => {
    window.history.replaceState(null, "", `/reset-password#token=${TOKEN}`);
    http.request.mockRejectedValue(new ApiError("bad_request"));
    const user = renderStrict(<ResetPasswordForm />);

    await user.type(await screen.findByLabelText("Nouveau mot de passe"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Changer mon mot de passe" }));

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", MESSAGES.invalidLink);
    expect(screen.getByRole("link", { name: "Demander un nouveau lien" })).toBeDefined();
    expect(screen.queryByLabelText("Nouveau mot de passe")).toBeNull();
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : un mot de passe refusé garde le formulaire : le lien reste utilisable.
  test("garde le formulaire quand le mot de passe est refusé", async () => {
    window.history.replaceState(null, "", `/reset-password#token=${TOKEN}`);
    http.request.mockRejectedValue(new ApiError("validation"));
    const user = renderStrict(<ResetPasswordForm />);

    await user.type(await screen.findByLabelText("Nouveau mot de passe"), PASSWORD);
    await user.click(screen.getByRole("button", { name: "Changer mon mot de passe" }));

    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      MESSAGES.passwordRefused,
    );
    expect(screen.getByLabelText("Nouveau mot de passe")).toBeDefined();
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : sans jeton dans le lien, aucun formulaire n'est proposé.
  test("refuse une page ouverte sans jeton", async () => {
    window.history.replaceState(null, "", "/reset-password");
    renderStrict(<ResetPasswordForm />);

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", MESSAGES.invalidLink);
    expect(screen.queryByLabelText("Nouveau mot de passe")).toBeNull();
  });
});

describe("vérification d'adresse", () => {
  // requires : rien.
  // assigns  : modifie l'adresse de la page simulée.
  // ensures  : le jeton est effacé de l'adresse et envoyé une seule fois, malgré le double
  //            montage du mode strict.
  test("vérifie une seule fois et efface le jeton", async () => {
    window.history.replaceState(null, "", `/verify-email#token=${TOKEN}`);
    http.request.mockResolvedValue(undefined);
    renderStrict(<VerifyEmailStatus />);

    expect(await screen.findByText(MESSAGES.emailVerified)).toBeDefined();
    expect(http.request).toHaveBeenCalledTimes(1);
    expect(http.request).toHaveBeenCalledWith(expect.objectContaining({ body: { token: TOKEN } }));
    expect(window.location.href).not.toContain(TOKEN);
  });

  // requires : rien.
  // assigns  : modifie l'adresse de la page simulée.
  // ensures  : un lien déjà utilisé donne le message de lien invalide.
  test("signale un lien déjà utilisé", async () => {
    window.history.replaceState(null, "", `/verify-email#token=${TOKEN}`);
    http.request.mockRejectedValue(new ApiError("bad_request"));
    renderStrict(<VerifyEmailStatus />);

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", MESSAGES.invalidLink);
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : une nouvelle adresse collée dans le même onglet est lue à son tour.
  test("lit un nouveau lien collé dans le même onglet", async () => {
    window.history.replaceState(null, "", "/verify-email");
    http.request.mockResolvedValue(undefined);
    renderStrict(<VerifyEmailStatus />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", MESSAGES.invalidLink);

    window.history.replaceState(null, "", `/verify-email#token=${TOKEN}`);
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    await waitFor(() => {
      expect(window.location.hash).toBe("");
    });
  });
});
