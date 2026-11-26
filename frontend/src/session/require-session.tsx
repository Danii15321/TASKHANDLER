"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useSession } from "@/session/use-session";

// Page de connexion, vers laquelle sont renvoyés les visiteurs sans session.
export const LOGIN_PATH = "/login";

// requires : `children` est le contenu d'une page privée.
// assigns  : redirige vers la connexion quand la session est absente.
// ensures  : n'affiche `children` qu'avec une session active. C'est un confort d'affichage, pas
//            un contrôle de sécurité : les données ne viennent que de l'API, qui vérifie le jeton
//            à chaque requête et répond `401` sans lui, quoi que fasse ce composant.
export function RequireSession({ children }: { children: ReactNode }): ReactNode {
  const { status } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (status === "anonymous") {
      router.replace(LOGIN_PATH);
    }
  }, [status, router]);

  if (status === "authenticated") {
    return children;
  }
  if (status === "unreachable") {
    return (
      <p role="alert" className="message error">
        Le service est momentanément indisponible. Nouvel essai automatique dans quelques instants.
      </p>
    );
  }
  return <p role="status">Vérification de votre session…</p>;
}
