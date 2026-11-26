"use client";

import Link from "next/link";
import { useActionState, type ReactNode } from "react";

import { INITIAL_FORM_STATE, submitRegister, type FormState } from "@/account/actions";
import { FormMessage } from "@/components/form-message";
import { TextField } from "@/components/text-field";
import { getBrowserHttpClient } from "@/session/browser-session";

// Aide affichée sous le mot de passe : longueur seulement, aucune règle de composition.
export const NEW_PASSWORD_HINT =
  "De 12 à 128 caractères. Une phrase de plusieurs mots est plus sûre et plus facile à retenir.";

// requires : `_previous` est l'état précédent ; `formData` vient du formulaire.
// assigns  : demande la création du compte.
// ensures  : renvoie le nouvel état du formulaire.
function register(_previous: FormState, formData: FormData): Promise<FormState> {
  return submitRegister(getBrowserHttpClient(), formData);
}

// requires : rendu dans le navigateur.
// assigns  : demande la création d'un compte à l'envoi.
// ensures  : affiche le formulaire, puis la même confirmation que l'adresse soit libre ou non.
export function RegisterForm(): ReactNode {
  const [state, formAction, pending] = useActionState(register, INITIAL_FORM_STATE);
  if (state.status === "success") {
    return <FormMessage state={state} />;
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
      <TextField
        name="password"
        label="Mot de passe"
        type="password"
        autoComplete="new-password"
        hint={NEW_PASSWORD_HINT}
        error={fields.password}
      />
      <button type="submit" disabled={pending}>
        {pending ? "Envoi…" : "Créer mon compte"}
      </button>
      <p>
        Déjà inscrit ? <Link href="/login">Se connecter</Link>
      </p>
    </form>
  );
}
