# Phase 7 — Vérification d'e-mail et réinitialisation

## Objectif

Des parcours de compte qui ne révèlent pas quelles adresses sont inscrites.

**Résultat attendu :** les réponses et les temps de traitement ne permettent pas de distinguer une
adresse connue d'une adresse inconnue.

Le code complet et commenté se trouve dans `backend/app/`. Ce guide en explique les décisions.

## Étape 1 — Envoyer des e-mails (`app/core/email.py`)

Aucune dépendance de plus : `smtplib` (bibliothèque standard), exécuté hors de la boucle
d'événements. `EmailSender` est une interface : SMTP en vrai, un enregistreur en mémoire dans les
tests. En développement, **Mailpit** (ajouté à `compose.yaml`) garde les e-mails et les affiche
sur `http://127.0.0.1:58025`.

| Règle | Risque évité |
|---|---|
| Texte brut, aucun HTML | Contenu actif injecté dans un e-mail |
| `EmailMessage` refuse les retours à la ligne dans les en-têtes | Injection d'un `Bcc:` |
| `ssl.create_default_context()` | Serveur SMTP usurpé |
| Hors développement : `tls` ou `starttls`, avec authentification | E-mail lu ou relayé en clair |

## Étape 2 — Les jetons à usage unique (`one_time_tokens`)

Même génération que les jetons de renouvellement (256 bits, stockés en SHA-256), plus un usage
(`email_verification` ou `password_reset`) et une date d'utilisation.

La consommation tient en **une seule instruction**, donc atomique :

```sql
UPDATE one_time_tokens SET used_at = now()
WHERE token_hash = :hash AND purpose = :purpose AND used_at IS NULL AND expires_at > now()
RETURNING user_id
```

Contraintes en base : usage dans une liste fermée, réinitialisation de 30 minutes au plus,
vérification de 72 heures au plus. Émettre un nouveau jeton supprime les anciens inutilisés.

> **Défaut corrigé : deux horloges.** L'expiration était d'abord calculée par Python, et la
> création datée par PostgreSQL (`now()`, figé au début de la transaction). L'écart a suffi à
> dépasser la limite de 30 minutes vérifiée par la base. L'expiration est désormais
> `now() + durée`, calculée par PostgreSQL : une seule horloge.

## Étape 3 — Des liens sans jeton dans l'URL

```text
https://app.exemple.fr/reset-password#token=…
```

Le lien pointe vers le **frontend**, jamais vers l'API, et le jeton est dans le **fragment** (`#`) :
un navigateur ne l'envoie jamais au serveur, il n'apparaît donc ni dans les journaux d'accès ni dans
l'en-tête `Referer`. Le frontend le lit, puis l'envoie dans le corps d'un `POST`.

## Étape 4 — Une inscription uniforme

```python
background_tasks.add_task(process_registration, ...)
return AcceptedResponse(status="accepted")   # 202
```

Avant de répondre, la route ne fait **que** valider la demande et compter dans Redis : rien qui
dépende de l'existence de l'adresse. Le reste se passe après la réponse :

| Situation | E-mail envoyé |
|---|---|
| Adresse libre | Lien de vérification |
| Inscrite, jamais vérifiée | Nouveau lien ; l'ancien ne fonctionne plus |
| Inscrite et vérifiée | « Un compte existe déjà avec cette adresse » |

Seul le propriétaire de l'adresse apprend qu'elle était inscrite. Une erreur pendant ce travail est
journalisée (`background_task_failed`, type seulement) : la réponse est déjà partie.

> **Piège FastAPI.** Une route qui **lève** une exception perd ses tâches d'arrière-plan. Quand une
> tâche doit partir avec une erreur, la route **renvoie** la réponse d'erreur, avec `background`.

## Étape 5 — Connexion, vérification et réinitialisation

- `POST /auth/verify-email` : consomme le jeton et date `email_verified_at` (`204`, ou `400`).
- Connexion avant vérification : `403`, **seulement après un mot de passe correct**, avec un
  nouveau lien (3 par heure au plus). Un mauvais mot de passe garde le `401` habituel.
- `POST /auth/password/forgot` : `202` à l'identique ; seul un compte actif reçoit un lien.
- `POST /auth/password/reset`, en une transaction : consommation du jeton, nouvelle empreinte,
  adresse marquée vérifiée, **toutes les sessions révoquées**, `token_version` incrémenté.

Si le nouveau mot de passe reprend l'adresse, la réponse est `422` et la transaction est annulée
par le service lui-même : le lien reste utilisable pour un nouvel essai.

Limites : 5 demandes par heure et par IP, 3 par adresse, pour l'inscription comme pour l'oubli.

## Étape 6 — Tester

Un client de test (`OutboxClient`) voit les e-mails envoyés : les tests suivent les liens comme un
utilisateur. Les tâches d'arrière-plan écrivent dans la transaction annulée du test.

- inscription : `202` identique, adresse libre, inscrite, ou non vérifiée ;
- liens : usage unique, expiration, usage croisé refusé, nouveau lien invalidant l'ancien ;
- réinitialisation : sessions et jetons d'accès coupés, lien réutilisable après un refus ;
- envoi réel vers Mailpit, et injection d'en-tête refusée ;
- aucun mot de passe, jeton ni adresse dans les journaux.

> **Fidélité des tests.** La session de test doit annuler la transaction sur erreur, comme
> `get_session` en production : sinon, un test peut passer alors que le code échouerait.

## Étape 7 — Mesurer sur un vrai serveur

Sur 32 requêtes alternées par route, un seul couple statut et corps observé. Avec une pause entre
les requêtes : 15,4 ms contre 14,5 ms pour l'inscription, 11,1 ms contre 16,1 ms pour l'oubli. Ce
sont des écarts de quelques millisecondes, dont le sens ne se reproduit pas.

> **Limite connue.** Sans pause, le travail d'arrière-plan d'une requête ralentit un peu la
> suivante. Une file de tâches dans un processus séparé (phase 11) supprimerait cet effet.

## À retenir

- Ce qui dépend d'un secret se fait après la réponse, ou jamais.
- Un jeton ne voyage jamais dans une URL envoyée au serveur.
- Une base qui vérifie une durée doit utiliser la même horloge que celle qui la date.

**Normes concernées :** OWASP API Security Top 10 2023, API2 et API6 ; OWASP ASVS, V2.5
(récupération d'identifiants) ; OWASP Forgot Password Cheat Sheet ; NIST SP 800-63B.
