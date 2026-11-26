"use client";

import Link from "next/link";
import { useActionState, type ReactNode } from "react";

import { INITIAL_FORM_STATE, submitForgotPassword, type FormState } from "@/account/actions";
import { FormMessage } from "@/components/form-message";
import { TextField } from "@/components/text-field";
import { getBrowserHttpClient } from "@/session/browser-session";

// requires : `_previous` est l'état précédent ; `formData` vient du formulaire.
// assigns  : demande l'envoi d'un lien de réinitialisation.
// ensures  : renvoie le nouvel état du formulaire.
function requestReset(_previous: FormState, formData: FormData): Promise<FormState> {
  return submitForgotPassword(getBrowserHttpClient(), formData);
}

// requires : rendu dans le navigateur.
// assigns  : demande un lien de réinitialisation à l'envoi.
// ensures  : affiche la même confirmation, que l'adresse corresponde à un compte ou non.
export function ForgotPasswordForm(): ReactNode {
  const [state, formAction, pending] = useActionState(requestReset, INITIAL_FORM_STATE);
  if (state.status === "success") {
    return (
      <>
        <FormMessage state={state} />
        <p>
          <Link href="/login">Retour à la connexion</Link>
        </p>
      </>
    );
  }
  const fields = state.status === "error" ? state.fields : {};

  return (
    <form action={formAction} noValidate>
      <FormMessage state={state} />
      <TextField
        name="email"
        label="Adresse e-mail"
        type="email"
        autoComplete="username"
        defaultValue={state.email}
        error={fields.email}
      />
      <button type="submit" disabled={pending}>
        {pending ? "Envoi…" : "Recevoir un lien"}
      </button>
    </form>
  );
}
