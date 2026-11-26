import Link from "next/link";
import type { ReactNode } from "react";

// requires : rien.
// assigns  : rien (composant sans effet de bord).
// ensures  : renvoie une page d'accueil statique, sans aucune donnée utilisateur.
export default function HomePage(): ReactNode {
  return (
    <main>
      <h1>TaskHandler</h1>
      <p>Planifiez vos tâches dans une grille hebdomadaire.</p>
      <p>
        <Link href="/login">Se connecter</Link> ou <Link href="/register">créer un compte</Link>.
      </p>
    </main>
  );
}
