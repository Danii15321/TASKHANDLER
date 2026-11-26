// @vitest-environment jsdom
// Tests de la garde de session et de l'en-tête dans un DOM simulé.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, test, vi } from "vitest";

import { LOGOUT_INCOMPLETE, SiteHeader } from "@/components/site-header";
import { RequireSession } from "@/session/require-session";
import type { SessionStatus } from "@/session/session-manager";

const { router, manager, session } = vi.hoisted(() => {
  const state: { status: SessionStatus } = { status: "unknown" };
  return {
    router: { replace: vi.fn(), push: vi.fn(), prefetch: vi.fn() },
    manager: { logout: vi.fn(), logoutAll: vi.fn() },
    session: state,
  };
});

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/session/browser-session", () => ({ getBrowserSessionManager: () => manager }));
vi.mock("@/session/use-session", () => ({ useSession: () => ({ status: session.status }) }));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("garde de session", () => {
  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : sans session, le contenu privé n'est jamais affiché et le visiteur est renvoyé
  //            vers la connexion.
  test("renvoie vers la connexion sans session", () => {
    session.status = "anonymous";

    render(<RequireSession>contenu privé</RequireSession>);

    expect(screen.queryByText("contenu privé")).toBeNull();
    expect(router.replace).toHaveBeenCalledWith("/login");
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : chaque état affiche ce qu'il doit, et seul `authenticated` montre le contenu.
  test.each<[SessionStatus, string]>([
    ["authenticated", "contenu privé"],
    ["unknown", "Vérification de votre session…"],
    ["unreachable", "Le service est momentanément indisponible."],
  ])("en état %s affiche « %s »", (status, expected) => {
    session.status = status;

    render(<RequireSession>contenu privé</RequireSession>);

    expect(screen.getByText(expected, { exact: false })).toBeDefined();
    expect(router.replace).not.toHaveBeenCalled();
  });
});

describe("en-tête", () => {
  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : sans session, l'en-tête propose la connexion et l'inscription ; tant que la
  //            session est inconnue, il ne propose rien.
  test("adapte la navigation à la session", () => {
    session.status = "anonymous";
    render(<SiteHeader />);
    expect(screen.getByRole("link", { name: "Connexion" })).toBeDefined();
    cleanup();

    session.status = "unknown";
    render(<SiteHeader />);
    expect(screen.queryByRole("link", { name: "Connexion" })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : chaque bouton ferme la bonne session, puis renvoie vers la connexion.
  test.each([
    ["Se déconnecter", "logout"],
    ["Déconnecter tous mes appareils", "logoutAll"],
  ] as const)("« %s » appelle %s", async (label, method) => {
    session.status = "authenticated";
    manager[method].mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<SiteHeader />);

    await user.click(screen.getByRole("button", { name: label }));

    await waitFor(() => {
      expect(router.replace).toHaveBeenCalledWith("/login");
    });
    expect(manager[method]).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  // requires : rien.
  // assigns  : rien hors du DOM simulé.
  // ensures  : une déconnexion non confirmée par l'API est signalée.
  test("signale une déconnexion non confirmée", async () => {
    session.status = "authenticated";
    manager.logout.mockRejectedValue(new Error("réseau"));
    const user = userEvent.setup();
    render(<SiteHeader />);

    await user.click(screen.getByRole("button", { name: "Se déconnecter" }));

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", LOGOUT_INCOMPLETE);
    expect(router.replace).toHaveBeenCalledWith("/login");
  });
});
