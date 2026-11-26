import type { ReactNode } from "react";

import type { FormState } from "@/account/actions";

// requires : `state` est l'état du formulaire.
// assigns  : rien (composant sans effet de bord).
// ensures  : annonce l'erreur (`role="alert"`) ou la réussite (`role="status"`) aux lecteurs
//            d'écran ; n'affiche rien avant le premier envoi.
export function FormMessage({ state }: { state: FormState }): ReactNode {
  if (state.status === "error") {
    return (
      <p role="alert" className="message error">
        {state.message}
      </p>
    );
  }
  if (state.status === "success" && state.message !== "") {
    return (
      <p role="status" className="message success">
        {state.message}
      </p>
    );
  }
  return null;
}
