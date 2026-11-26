import type { Metadata } from "next";
import { connection } from "next/server";
import type { ReactNode } from "react";

import { SiteHeader } from "@/components/site-header";
import { SessionBootstrap } from "@/session/session-bootstrap";

import "./globals.css";

export const metadata: Metadata = {
  title: "TaskHandler",
  description: "Planification de tâches dans une grille hebdomadaire.",
};

// requires : `children` est le contenu de la page courante, rendu par Next.js.
// assigns  : rien (composant sans effet de bord).
// ensures  : renvoie le document HTML racine, déclaré en français pour les lecteurs d'écran,
//            rendu à chaque requête.
export default async function RootLayout({
  children,
}: {
  children: ReactNode;
}): Promise<ReactNode> {
  // Le nonce de la CSP change à chaque requête : une page générée une fois pour toutes au build
  // n'en aurait aucun, et ses scripts seraient bloqués. `connection()` impose donc le rendu à la
  // requête pour toute l'application.
  await connection();

  return (
    <html lang="fr">
      <body>
        <SessionBootstrap />
        <SiteHeader />
        {children}
      </body>
    </html>
  );
}
