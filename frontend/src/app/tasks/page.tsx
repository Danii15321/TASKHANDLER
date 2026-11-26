import type { Metadata } from "next";
import type { ReactNode } from "react";

import { RequireSession } from "@/session/require-session";

export const metadata: Metadata = { title: "Mes tâches — TaskHandler" };

// requires : rien.
// assigns  : rien (composant serveur sans effet de bord).
// ensures  : affiche l'espace des tâches, réservé aux visiteurs connectés. Les tâches elles-mêmes
//            arrivent en phase 6.
export default function Page(): ReactNode {
  return (
    <main>
      <h1>Mes tâches</h1>
      <RequireSession>
        <p>Vous êtes connecté. La liste de vos tâches s’affichera ici.</p>
      </RequireSession>
    </main>
  );
}
