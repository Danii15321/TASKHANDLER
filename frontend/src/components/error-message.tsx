import type { ReactNode } from "react";

// requires : `onRetry` relance le rendu du segment en erreur.
// assigns  : rien (composant sans effet de bord ; le clic délègue à `onRetry`).
// ensures  : affiche un message générique et un bouton « Réessayer ». Aucun détail technique
//            (message d'exception, pile, requête) n'est jamais affiché.
export function ErrorMessage({ onRetry }: { onRetry: () => void }): ReactNode {
  return (
    <main>
      <h1>Une erreur est survenue</h1>
      <p>La page n’a pas pu s’afficher. Réessayez dans un instant.</p>
      <button type="button" onClick={onRetry}>
        Réessayer
      </button>
    </main>
  );
}
