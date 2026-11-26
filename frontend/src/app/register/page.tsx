import type { Metadata } from "next";
import type { ReactNode } from "react";

import { RegisterForm } from "@/account/register-form";

export const metadata: Metadata = { title: "Créer un compte — TaskHandler" };

// requires : rien.
// assigns  : rien (composant serveur sans effet de bord).
// ensures  : affiche le formulaire d’inscription.
export default function Page(): ReactNode {
  return (
    <main>
      <h1>Créer un compte</h1>
      <RegisterForm />
    </main>
  );
}
