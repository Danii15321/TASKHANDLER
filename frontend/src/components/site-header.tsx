"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition, type ReactNode } from "react";

import { LOGIN_PATH } from "@/session/require-session";
import { getBrowserSessionManager } from "@/session/browser-session";
import { useSession } from "@/session/use-session";

// Affiché si l'API n'a pas confirmé la déconnexion : la session locale est fermée, mais le
// serveur peut encore la connaître.
export const LOGOUT_INCOMPLETE =
  "La déconnexion n’a pas pu être confirmée par le serveur. Réessayez quand la connexion sera " +
  "rétablie.";

// requires : rendu dans le navigateur, dans la mise en page racine.
// assigns  : ferme la session sur demande, puis renvoie vers la connexion.
// ensures  : affiche la navigation adaptée à la session : connexion et inscription sans session,
//            tâches et déconnexions avec ; rien tant que la session n'est pas connue.
export function SiteHeader(): ReactNode {
  const { status } = useSession();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  // requires : `everywhere` choisit la déconnexion de tous les appareils.
  // assigns  : ferme la session ; met à jour le message d'erreur ; change de page.
  // ensures  : la session locale est fermée dans tous les cas ; un échec côté API est signalé.
  function closeSession(everywhere: boolean): void {
    startTransition(async () => {
      const manager = getBrowserSessionManager();
      try {
        await (everywhere ? manager.logoutAll() : manager.logout());
        setError(null);
      } catch {
        setError(LOGOUT_INCOMPLETE);
      }
      router.replace(LOGIN_PATH);
    });
  }

  return (
    <header className="site-header">
      <nav aria-label="Navigation principale">
        <Link href="/">TaskHandler</Link>
        {status === "authenticated" ? (
          <>
            <Link href="/tasks">Mes tâches</Link>
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                closeSession(false);
              }}
            >
              Se déconnecter
            </button>
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                closeSession(true);
              }}
            >
              Déconnecter tous mes appareils
            </button>
          </>
        ) : null}
        {status === "anonymous" ? (
          <>
            <Link href="/login">Connexion</Link>
            <Link href="/register">Créer un compte</Link>
          </>
        ) : null}
      </nav>
      {error === null ? null : (
        <p role="alert" className="message error">
          {error}
        </p>
      )}
    </header>
  );
}
