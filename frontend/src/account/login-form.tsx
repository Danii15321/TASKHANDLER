"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useActionState, type ReactNode } from "react";

import { INITIAL_FORM_STATE, submitLogin, type FormState } from "@/account/actions";
import { FormMessage } from "@/components/form-message";
import { TextField } from "@/components/text-field";
import { getBrowserSessionManager } from "@/session/browser-session";

// Page d'arrivée après connexion. Fixe, jamais lue dans l'URL : pas de redirection ouverte.
export const AFTER_LOGIN_PATH = "/tasks";

// requires : rendu dans le navigateur.
// assigns  : ouvre une session à l'envoi, puis redirige vers les tâches.
// ensures  : affiche le formulaire de connexion ; un échec garde l'adresse saisie, jamais le mot
//            de passe, et n'affiche qu'un message unique.
export function LoginForm(): ReactNode {
  const router = useRouter();
  const [state, formAction, pending] = useActionState(
    async (_previous: FormState, formData: FormData): Promise<FormState> => {
      const result = await submitLogin(getBrowserSessionManager(), formData);
      if (result.status === "success") {
        router.replace(AFTER_LOGIN_PATH);
      }
      return result;
    },
    INITIAL_FORM_STATE,
  );
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
        autoComplete="current-password"
        error={fields.password}
      />
      <button type="submit" disabled={pending}>
        {pending ? "Connexion…" : "Se connecter"}
      </button>
      <p>
        <Link href="/forgot-password">Mot de passe oublié ?</Link>
      </p>
      <p>
        Pas encore de compte ? <Link href="/register">Créer un compte</Link>
      </p>
    </form>
  );
}
