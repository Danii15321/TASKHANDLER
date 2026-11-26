import Link from "next/link";
import type { ReactNode } from "react";

// requires : rien.
// assigns  : rien (composant sans effet de bord).
// ensures  : affiche une page « introuvable » qui ne cite ni l'adresse demandée ni aucun détail
//            sur l'existence d'une ressource.
export default function NotFound(): ReactNode {
  return (
    <main>
      <h1>Page introuvable</h1>
      <p>Cette page n’existe pas ou n’est pas accessible.</p>
      <Link href="/">Retour à l’accueil</Link>
    </main>
  );
}
