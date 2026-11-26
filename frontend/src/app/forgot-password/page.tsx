import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ForgotPasswordForm } from "@/account/forgot-password-form";

export const metadata: Metadata = { title: "Mot de passe oublié — TaskHandler" };

// requires : rien.
// assigns  : rien (composant serveur sans effet de bord).
// ensures  : affiche la demande de lien de réinitialisation.
export default function Page(): ReactNode {
  return (
    <main>
      <h1>Mot de passe oublié</h1>
      <ForgotPasswordForm />
    </main>
  );
}
