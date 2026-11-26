# Phase 5 du frontend — Parcours de compte

## Objectif

Les écrans d'inscription, de vérification d'adresse, de connexion, de mot de passe oublié, de
réinitialisation et de déconnexion. Ils reprennent les garanties de l'API : **aucun écran ne
permet de savoir si une adresse est inscrite**, et aucun jeton à usage unique ne reste dans
l'adresse de la page.

**Résultat attendu :** les écrans et les messages ne distinguent pas une adresse connue d'une
adresse inconnue, et aucun jeton à usage unique ne reste dans l'historique de l'onglet.

## Étape 1 — Schémas des routes de compte

`src/api/schemas.ts` gagne `RegisterRequest`, `VerifyEmailRequest`, `ForgotPasswordRequest`,
`ResetPasswordRequest` et `AcceptedResponse`, aux limites de l'API (12 à 128 caractères, 320 pour
une adresse, jeton de 43 caractères). `contract.test.ts` les compare au contrat, comme en phase 4.

## Étape 2 — Le jeton dans le fragment

Les liens envoyés par e-mail portent le jeton dans le fragment : `/verify-email#token=…`. Un
fragment ne part jamais vers le serveur (ni journaux, ni `Referer`), mais il reste dans la barre
d'adresse et l'historique de l'onglet. `src/account/fragment-token.ts` :

1. copie le fragment, puis l'**efface aussitôt** par `history.replaceState` (et non `pushState` :
   l'entrée qui le contenait est remplacée, pas conservée derrière « Précédent ») ;
2. valide le format exact du jeton ; un fragment invalide est effacé lui aussi ;
3. garde le jeton en mémoire le temps de l'envoyer dans un corps `POST`, puis l'oublie
   (`forgetFragmentToken`).

Le hook `useFragmentToken` passe par `useSyncExternalStore` : le serveur ne voit jamais le
fragment (`undefined`), et un nouveau lien collé dans le même onglet (`hashchange`) est relu.

> **Défaut trouvé par les tests.** La première version lisait `location.hash` *après* l'avoir
> effacé. Avec un objet simulé figé, rien ne se voyait ; avec le vrai `window.location` de jsdom,
> qui est vivant, le jeton était perdu. Le test en vrai DOM l'a révélé ; d'où la copie préalable.

> **Limite à connaître.** Ce nettoyage couvre l'adresse et l'historique de l'onglet. L'historique
> global du navigateur (`chrome://history`) peut avoir enregistré l'adresse d'arrivée : aucune page
> ne peut l'effacer. La protection repose alors sur l'API : jeton à usage unique, consommé dès
> l'ouverture (vérification) ou au changement de mot de passe, et expirant en 30 minutes au plus.

## Étape 3 — La logique, séparée des écrans

`src/account/actions.ts` contient des fonctions pures, qui reçoivent leurs dépendances (client
HTTP, gestionnaire de session) et renvoient l'état du formulaire :

| Parcours | Message en cas de réussite | Particularité de sécurité |
|---|---|---|
| Inscription | « Si cette adresse peut être utilisée, un e-mail vient d'y être envoyé… » | Même texte, adresse libre ou prise |
| Mot de passe oublié | « Si un compte correspond à cette adresse… » | Même texte, adresse connue ou non |
| Connexion | Redirection vers `/tasks` | Un seul message d'échec : `Identifiants invalides.` |
| Vérification | « Votre adresse est vérifiée… » | Jeton envoyé une seule fois |
| Réinitialisation | « Votre mot de passe a été changé. Toutes vos sessions ont été fermées. » | Lien refusé → nouvelle demande ; mot de passe refusé → le lien reste utilisable |

- Le mot de passe n'est **jamais** renvoyé dans l'état du formulaire : seule l'adresse l'est,
  pour la réafficher après une erreur.
- La longueur se compte en points de code après NFKC (`Array.from`), comme `len` en Python :
  `.length` compterait deux unités pour un émoji.
- **Aucune règle de composition** (majuscule, chiffre, symbole) : elles n'améliorent pas la
  sécurité réelle (NIST SP 800-63B). Seule la longueur est guidée ; les mots de passe courants et
  ceux qui reprennent l'adresse sont refusés par l'API, avec un message explicatif.
- Le `403` de la connexion (adresse non vérifiée, un nouveau lien part) reçoit un message prudent,
  qui vaut aussi pour un refus anti-CSRF ; un `429` indique le délai exact à attendre.

## Étape 4 — Les écrans

Chaque formulaire est un composant client branché sur `useActionState`, qui appelle l'action
correspondante. Les pages (`src/app/*/page.tsx`) restent des composants serveur qui n'ajoutent
qu'un titre.

`TextField` porte les règles d'accessibilité et de confort :

- libellé relié au champ, aide et erreur annoncées (`aria-describedby`, `aria-invalid`), messages
  en `role="alert"` ou `role="status"` ;
- `autocomplete` exact : `username`, `current-password`, `new-password`, pour que les gestionnaires
  de mots de passe remplissent et proposent le bon secret ;
- **pas de `maxLength`** : le navigateur tronquerait en silence un mot de passe collé trop long ;
- **collage jamais bloqué** (WCAG 2.2, critère 3.3.8 : authentification accessible) ;
- `noValidate` sur le formulaire : les messages sont ceux de l'application, cohérents et en
  français, et non les infobulles du navigateur.

La page d'arrivée après connexion est **fixe** (`/tasks`) : aucune adresse de retour n'est lue
dans l'URL, ce qui écarte toute redirection ouverte.

## Étape 5 — Garde de session et déconnexion

- `RequireSession` n'affiche le contenu qu'avec une session active, renvoie vers `/login` sans
  session et signale une API injoignable. C'est un **confort d'affichage** : les données ne
  viennent que de l'API, qui exige le jeton à chaque requête.
- `SiteHeader` adapte la navigation à la session. Ses deux boutons appellent `logout` ou
  `logoutAll`, puis ramènent à la connexion ; une déconnexion non confirmée par l'API est signalée.
- `src/app/globals.css` assure lisibilité, contraste et focus visible, sans aucune ressource
  externe (la CSP n'autorise que le site).

## Étape 6 — Tests en DOM simulé

Nouvelles dépendances de développement, validées : **jsdom** 31.0.1 (DOM de référence, préféré à
happy-dom, qui a connu une faille d'exécution de code), **@testing-library/react** 16.3.3,
**@testing-library/dom** 10.4.2 et **@testing-library/user-event** 14.6.13. Un fichier de test
choisit le DOM par la ligne `// @vitest-environment jsdom` ; les autres restent en Node.

Les écrans sont rendus en `StrictMode`, comme sous `next dev`. Les tests prouvent notamment :

- mot de passe **collé** arrivé entier, champs sans `maxlength`, `autocomplete` exacts ;
- même écran, octet pour octet, après l'inscription de deux adresses différentes ;
- jeton absent de `window.location` dès l'affichage, envoyé dans le corps, **une seule fois**
  malgré le double montage du mode strict ;
- après un échec de connexion : message unique, adresse conservée, mot de passe vidé ;
- garde : contenu privé jamais affiché sans session.

> **Piège.** Les tests reçoivent eux aussi les règles anti-XSS d'ESLint : même lire
> `document.body.innerHTML` est refusé. On compare `textContent`.

## Vérification

```bash
pnpm run lint && pnpm run typecheck && pnpm run format:check && pnpm run test:coverage
```

Dans un vrai navigateur (Chrome), avec l'API lancée et un compte jetable supprimé ensuite :

| Vérification | Résultat |
|---|---|
| Inscription d'une adresse neuve, puis de la même adresse | Texte de page strictement identique |
| Lien de vérification ouvert depuis Mailpit | Adresse vérifiée ; jeton absent de l'URL, historique de même longueur |
| « Précédent » puis « Suivant » | L'entrée restaurée est `/verify-email`, sans jeton |
| Mauvais mot de passe, puis adresse inconnue | Même écran : `Identifiants invalides.` ; adresse gardée, mot de passe vidé |
| Connexion réussie, puis « Se déconnecter » | `/tasks`, puis retour à `/login` ; `/tasks` rechargée renvoie à la connexion |
| Mot de passe oublié, adresse connue puis inconnue | Même confirmation ; aucun e-mail envoyé à l'adresse inconnue |
| Réinitialisation par le lien | Mot de passe changé ; ancien refusé (`401`), nouveau accepté |
| Même lien rouvert et soumis | « Ce lien est invalide, expiré ou déjà utilisé » |

Pendant ces essais, l'extension de pilotage de Chrome se déconnectait à chaque **frappe clavier
simulée** : les champs ont été remplis directement, puis les formulaires envoyés par un clic.

## Ce que cette phase ne fait pas

Pas encore de tâches (phase 6), ni de profil, de changement de mot de passe connecté ou de
suppression du compte (phase 8).
