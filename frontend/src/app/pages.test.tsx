import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, test } from "vitest";

import ErrorPage from "@/app/error";
import GlobalError from "@/app/global-error";
import NotFound from "@/app/not-found";

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
