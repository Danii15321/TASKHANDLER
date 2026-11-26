import type { ReactNode } from "react";

// requires : `children` est le contenu de la page courante, rendu par Next.js.
// assigns  : rien (composant sans effet de bord).
// ensures  : renvoie le document HTML racine, déclaré en français pour les lecteurs d'écran.
export default function RootLayout({ children }: { children: ReactNode }): ReactNode {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
