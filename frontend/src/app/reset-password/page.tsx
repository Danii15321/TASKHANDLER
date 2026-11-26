import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ResetPasswordForm } from "@/account/reset-password-form";

export const metadata: Metadata = { title: "Nouveau mot de passe — TaskHandler" };

// requires : rien.
// assigns  : rien (composant serveur sans effet de bord).
// ensures  : affiche le formulaire ouvert par le lien de réinitialisation.
export default function Page(): ReactNode {
  return (
    <main>
      <h1>Nouveau mot de passe</h1>
      <ResetPasswordForm />
    </main>
  );
}
