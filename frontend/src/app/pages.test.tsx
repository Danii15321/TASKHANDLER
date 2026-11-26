import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test, vi } from "vitest";

import ErrorPage from "@/app/error";
import GlobalError from "@/app/global-error";
import RootLayout from "@/app/layout";
import NotFound from "@/app/not-found";
import HomePage from "@/app/page";

// `connection()` attend normalement une requête HTTP réelle. Hors serveur, on le remplace par une
// promesse résolue : le test porte sur le document produit, pas sur le mode de rendu.
vi.mock("next/server", () => ({ connection: (): Promise<void> => Promise.resolve() }));

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
