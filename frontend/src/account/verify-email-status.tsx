"use client";

import Link from "next/link";
import { startTransition, useActionState, useEffect, useRef, type ReactNode } from "react";

import { INITIAL_FORM_STATE, verifyEmail, type FormState } from "@/account/actions";
import { forgetFragmentToken, useFragmentToken } from "@/account/fragment-token";
import { FormMessage } from "@/components/form-message";
import { getBrowserHttpClient } from "@/session/browser-session";

// requires : `_previous` est l'état précédent ; `token` le jeton lu dans le lien, ou `null`.
// assigns  : demande la vérification de l'adresse ; oublie ensuite le jeton.
// ensures  : renvoie l'issue de la vérification.
async function verify(_previous: FormState, token: string | null): Promise<FormState> {
  const result = await verifyEmail(getBrowserHttpClient(), token);
  forgetFragmentToken();
  return result;
}

// requires : rendu dans le navigateur, sur la page ouverte par le lien reçu par e-mail.
// assigns  : efface le jeton de l'adresse, puis l'envoie une seule fois à l'API.
// ensures  : affiche l'issue de la vérification et le lien utile pour continuer.
export function VerifyEmailStatus(): ReactNode {
  const token = useFragmentToken();
  const [state, dispatch] = useActionState(verify, INITIAL_FORM_STATE);
  // Le mode strict de React exécute deux fois les effets en développement : le jeton ne doit
  // partir qu'une fois, sans quoi le second envoi échouerait (jeton déjà consommé).
  const started = useRef(false);

  useEffect(() => {
    if (token === undefined || started.current) {
      return;
    }
    started.current = true;
    startTransition(() => {
      dispatch(token);
    });
  }, [token, dispatch]);

  if (state.status === "idle") {
    return <p role="status">Vérification de votre adresse…</p>;
  }
  return (
    <>
      <FormMessage state={state} />
      <p>
        <Link href="/login">Se connecter</Link>
      </p>
    </>
  );
}
