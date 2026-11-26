"use client";

import type { ReactNode } from "react";

import { ErrorMessage } from "@/components/error-message";

// requires : appelé par Next.js quand la mise en page racine elle-même échoue.
// assigns  : rien.
// ensures  : remplace tout le document par le message générique, en français ; l'erreur reçue
//            n'est jamais lue ni affichée.
export default function GlobalError({ retry }: { error: unknown; retry: () => void }): ReactNode {
  // Ce composant remplace la mise en page racine : il doit fournir `<html>` et `<body>`.
  return (
    <html lang="fr">
      <body>
        <title>Erreur — TaskHandler</title>
        <ErrorMessage onRetry={retry} />
      </body>
    </html>
  );
}
