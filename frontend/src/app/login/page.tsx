import type { Metadata } from "next";
import type { ReactNode } from "react";

import { LoginForm } from "@/account/login-form";

export const metadata: Metadata = { title: "Connexion — TaskHandler" };

// requires : rien.
// assigns  : rien (composant serveur sans effet de bord).
// ensures  : affiche le formulaire de connexion.
export default function Page(): ReactNode {
  return (
    <main>
      <h1>Connexion</h1>
      <LoginForm />
    </main>
  );
}
