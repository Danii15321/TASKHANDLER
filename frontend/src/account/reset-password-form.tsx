"use client";

import Link from "next/link";
import { useActionState, type ReactNode } from "react";

import {
  INITIAL_FORM_STATE,
  MESSAGES,
  submitResetPassword,
  type FormState,
} from "@/account/actions";
import { forgetFragmentToken, useFragmentToken } from "@/account/fragment-token";
import { NEW_PASSWORD_HINT } from "@/account/register-form";
import { FormMessage } from "@/components/form-message";
import { TextField } from "@/components/text-field";
import { getBrowserHttpClient } from "@/session/browser-session";

// requires : rendu dans le navigateur, sur la page ouverte par le lien reçu par e-mail.
// assigns  : efface le jeton de l'adresse dès le premier rendu ; change le mot de passe à l'envoi.
// ensures  : affiche le formulaire si le lien porte un jeton valide, un message sinon. Après
//            réussite, le jeton est oublié de la mémoire.
export function ResetPasswordForm(): ReactNode {
  const token = useFragmentToken();
  const [state, formAction, pending] = useActionState(
    async (_previous: FormState, formData: FormData): Promise<FormState> => {
      if (typeof token !== "string") {
        return { status: "error", email: "", message: MESSAGES.invalidLink, fields: {} };
      }
      const result = await submitResetPassword(getBrowserHttpClient(), token, formData);
      const linkUsedUp =
        result.status === "success" ||
        (result.status === "error" && result.message === MESSAGES.invalidLink);
      if (linkUsedUp) {
        forgetFragmentToken();
      }
      return result;
    },
    INITIAL_FORM_STATE,
  );

  if (token === undefined) {
    return <p role="status">Lecture du lien…</p>;
  }
  if (state.status === "success") {
    return (
      <>
        <FormMessage state={state} />
        <p>
          <Link href="/login">Se connecter</Link>
        </p>
      </>
    );
  }
  if (token === null || (state.status === "error" && state.message === MESSAGES.invalidLink)) {
    return (
      <>
        <p role="alert" className="message error">
          {MESSAGES.invalidLink}
        </p>
        <p>
          <Link href="/forgot-password">Demander un nouveau lien</Link>
        </p>
      </>
    );
  }
  const fields = state.status === "error" ? state.fields : {};

  return (
    <form action={formAction} noValidate>
      <FormMessage state={state} />
      <TextField
        name="password"
        label="Nouveau mot de passe"
        type="password"
        autoComplete="new-password"
        hint={NEW_PASSWORD_HINT}
        error={fields.password}
      />
      <button type="submit" disabled={pending}>
        {pending ? "Enregistrement…" : "Changer mon mot de passe"}
      </button>
    </form>
  );
}
