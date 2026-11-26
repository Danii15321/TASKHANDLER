import type { Metadata } from "next";
import type { ReactNode } from "react";

import { VerifyEmailStatus } from "@/account/verify-email-status";

export const metadata: Metadata = { title: "Vérification de l’adresse — TaskHandler" };

// requires : rien.
// assigns  : rien (composant serveur sans effet de bord).
// ensures  : vérifie l’adresse à partir du lien reçu par e-mail.
export default function Page(): ReactNode {
  return (
    <main>
      <h1>Vérification de l’adresse</h1>
      <VerifyEmailStatus />
    </main>
  );
}
