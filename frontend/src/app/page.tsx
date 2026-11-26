import type { ReactNode } from "react";

// requires : rien.
// assigns  : rien (composant sans effet de bord).
// ensures  : renvoie une page d'accueil statique, sans aucune donnée utilisateur.
export default function HomePage(): ReactNode {
  return (
    <main>
      <h1>TaskHandler</h1>
    </main>
  );
}
