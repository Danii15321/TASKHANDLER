import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test, vi } from "vitest";

import ErrorPage from "@/app/error";
import GlobalError from "@/app/global-error";
import RootLayout from "@/app/layout";
import NotFound from "@/app/not-found";
import ForgotPasswordPage from "@/app/forgot-password/page";
import LoginPage from "@/app/login/page";
import HomePage from "@/app/page";
import RegisterPage from "@/app/register/page";
import ResetPasswordPage from "@/app/reset-password/page";
import TasksPage from "@/app/tasks/page";
import VerifyEmailPage from "@/app/verify-email/page";

// `connection()` attend normalement une requête HTTP réelle. Hors serveur, on le remplace par une
// promesse résolue : le test porte sur le document produit, pas sur le mode de rendu.
vi.mock("next/server", () => ({ connection: (): Promise<void> => Promise.resolve() }));
// Les composants clients de la mise en page utilisent le routeur, absent hors de Next.js.
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));

// Message d'erreur interne qu'aucune page ne doit jamais afficher.
const INTERNAL_DETAIL = 'relation "users" : SELECT password_hash FROM users';

// requires : rien.
// assigns  : rien.
// ensures  : ne fait rien ; sert de fonction `retry` factice.
function noop(): void {
  // Aucun effet : le rendu statique ne déclenche jamais de clic.
}

describe("pages d'erreur", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : la page d'erreur affiche un message générique, sans le détail de l'exception.
  test("la page d'erreur n'affiche aucun détail technique", () => {
    const html = renderToStaticMarkup(
      <ErrorPage error={new Error(INTERNAL_DETAIL)} retry={noop} />,
    );

    expect(html).toContain("Une erreur est survenue");
    expect(html).not.toContain("password_hash");
    expect(html).not.toContain("users");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : l'erreur globale fournit son document en français, sans détail technique.
  test("l'erreur globale est en français et n'affiche aucun détail technique", () => {
    const html = renderToStaticMarkup(
      <GlobalError error={new Error(INTERNAL_DETAIL)} retry={noop} />,
    );

    expect(html).toContain('<html lang="fr">');
    expect(html).not.toContain("password_hash");
  });

  // requires : rien.
  // assigns  : rien.
  // ensures  : la page 404 reste générique.
  test("la page introuvable reste générique", () => {
    const html = renderToStaticMarkup(<NotFound />);

    expect(html).toContain("Page introuvable");
    expect(html).toContain('href="/"');
  });
});

describe("mise en page", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : le document racine est déclaré en français et contient la page.
  test("la mise en page racine déclare le français", async () => {
    const html = renderToStaticMarkup(await RootLayout({ children: <HomePage /> }));

    expect(html).toContain('<html lang="fr">');
    expect(html).toContain("<h1>TaskHandler</h1>");
  });
});

describe("pages de compte", () => {
  // requires : rien.
  // assigns  : rien.
  // ensures  : chaque page affiche son titre ; le rendu serveur ne contient aucune donnée de
  //            session, et la page privée n'affiche que l'attente de vérification.
  test.each([
    [LoginPage, "Connexion"],
    [RegisterPage, "Créer un compte"],
    [ForgotPasswordPage, "Mot de passe oublié"],
    [ResetPasswordPage, "Nouveau mot de passe"],
    [VerifyEmailPage, "Vérification de l’adresse"],
    [TasksPage, "Mes tâches"],
  ])("%#", (Page, title) => {
    const html = renderToStaticMarkup(<Page />);

    expect(html).toContain(`<h1>${title}</h1>`);
    expect(html).not.toContain("Vous êtes connecté");
  });
});
