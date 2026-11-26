import { useId, type HTMLInputAutoCompleteAttribute, type ReactNode } from "react";

// Propriétés d'un champ de formulaire.
export interface TextFieldProps {
  readonly name: string;
  readonly label: string;
  readonly type: "email" | "password" | "text";
  // `username`, `current-password`, `new-password` : les gestionnaires de mots de passe s'en
  // servent pour remplir ou proposer le bon secret au bon endroit.
  readonly autoComplete: HTMLInputAutoCompleteAttribute;
  readonly defaultValue?: string | undefined;
  readonly error?: string | undefined;
  readonly hint?: string | undefined;
}

// requires : `name` est unique dans le formulaire.
// assigns  : rien (composant sans effet de bord).
// ensures  : affiche un libellé relié au champ, une aide et une erreur annoncées par les lecteurs
//            d'écran (`aria-describedby`, `aria-invalid`). Aucun `maxLength` : le navigateur
//            tronquerait en silence un mot de passe collé trop long, que l'API refuserait sans
//            que l'utilisateur comprenne pourquoi. Le collage n'est jamais bloqué.
export function TextField(props: TextFieldProps): ReactNode {
  const id = useId();
  const hintId = `${id}-aide`;
  const errorId = `${id}-erreur`;
  const describedBy = [
    props.hint === undefined ? undefined : hintId,
    props.error === undefined ? undefined : errorId,
  ]
    .filter((value) => value !== undefined)
    .join(" ");

  return (
    <div className="field">
      <label htmlFor={id}>{props.label}</label>
      {props.hint === undefined ? null : (
        <p id={hintId} className="hint">
          {props.hint}
        </p>
      )}
      <input
        id={id}
        name={props.name}
        type={props.type}
        autoComplete={props.autoComplete}
        defaultValue={props.defaultValue}
        aria-invalid={props.error !== undefined}
        aria-describedby={describedBy === "" ? undefined : describedBy}
        autoCapitalize="none"
        spellCheck={false}
        required
      />
      {props.error === undefined ? null : (
        <p id={errorId} className="field-error">
          {props.error}
        </p>
      )}
    </div>
  );
}
