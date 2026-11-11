# Phase 5 — Authentification et sessions

## Objectif

Des sessions courtes, révocables, et résistantes au vol de jeton.

**Résultat attendu :** la réutilisation d'un jeton de renouvellement coupe toute la session
associée, y compris les jetons d'accès déjà émis.

Le code complet et commenté se trouve dans `backend/app/`. Ce guide en explique les décisions.

## Étape 1 — Deux jetons, deux rôles

| Jeton | Forme | Durée | Stockage serveur |
|---|---|---|---|
| Accès | JWT signé HS256 | 15 minutes, fixe dans le code | Aucun |
| Renouvellement | 43 caractères aléatoires (`secrets.token_urlsafe(32)`) | 7 jours d'inactivité, 30 jours au plus | Empreinte SHA-256 |

Le jeton d'accès accompagne chaque requête : il doit être vérifiable vite. Le jeton de
renouvellement ne sert qu'à en obtenir un nouveau : il est opaque et révocable.

Les deux voyagent dans le corps ou dans l'en-tête `Authorization`, **jamais dans une URL** :
une URL finit dans les journaux, l'historique et l'en-tête `Referer`.

## Étape 2 — La clé de signature

```bash
uv add pyjwt
```

PyJWT, et jamais `python-jose`, dont la maintenance a longtemps été défaillante. Ajoutez
`jwt_secret: SecretStr` à `Settings`, **sans valeur par défaut**, et refusez une clé :

- de moins de 64 caractères (64 caractères hexadécimaux = 256 bits) ;
- trop répétitive (`abab…`), ou contenant un mot d'exemple.

`scripts/generate-dev-env.sh` la génère avec `openssl rand -hex 32`. Qui détient cette clé peut
fabriquer un jeton pour n'importe quel compte : elle se traite comme un mot de passe administrateur.

## Étape 3 — Émettre et vérifier (`app/core/tokens.py`)

Revendications émises : `iss`, `aud`, `sub` (l'UUID du compte, **aucune** donnée personnelle),
`iat`, `exp`, `jti`, plus `sid` (la session) et `ver` (la version de jeton du compte).

```python
jwt.decode(token, secret, algorithms=["HS256"], audience=JWT_AUDIENCE, issuer=JWT_ISSUER,
           options={"require": ["iss", "aud", "sub", "iat", "exp", "jti", "sid", "ver"]})
```

> **Sécurité : confusion d'algorithme.** La liste `algorithms` est fixe. Si elle était lue dans
> l'en-tête du jeton, un attaquant choisirait `none` (aucune signature) ou un autre algorithme.
> `require` impose la présence de `exp` : sans lui, un jeton serait valable pour toujours.

Toute erreur devient `InvalidAccessTokenError`, sans préciser la cause.

## Étape 4 — La table `refresh_tokens`

Une **session** (`family_id`) regroupe tous les jetons nés d'une même connexion. Contraintes en
base : empreinte de 64 caractères hexadécimaux et unique, `expires_at <= session_expires_at`,
date et raison de révocation toujours ensemble, raison dans une liste fermée, suppression en
cascade avec le compte.

SHA-256 suffit pour ces jetons, alors qu'il est interdit pour les mots de passe : 256 bits d'aléa
ne se devinent pas, alors qu'un mot de passe humain se devine.

## Étape 5 — La connexion (`POST /api/v1/auth/login`)

Le service `authenticate` exécute **exactement une** vérification argon2id dans chaque cas :

| Cas | Vérification | Réponse |
|---|---|---|
| Adresse inconnue | Empreinte leurre, calculée au démarrage | `401 Identifiants invalides.` |
| Mot de passe faux | Vraie empreinte | Identique |
| Compte inactif | Vraie empreinte, **puis** test de l'état | Identique |

Le temps de réponse ne révèle donc pas l'existence du compte. Mesure sur le vrai serveur, 80 paires
alternées : 185 ms contre 186 ms au minimum, 1,3 % d'écart des médianes.

> **Piège de mesure.** Une première mesure, en deux séries successives, montrait 33 % d'écart :
> c'était l'ordre des séries, pas le code. Alternez toujours les cas comparés.

Après succès, si `check_needs_rehash` le demande, l'empreinte est recalculée avec les paramètres
actuels : c'est le seul moment où le mot de passe en clair est disponible. Le mot de passe reçu est
normalisé en NFKC, comme à l'inscription. La réponse porte `Cache-Control: no-store`.

## Étape 6 — `get_current_user` (`app/modules/auth/dependencies.py`)

La signature ne suffit pas : à chaque requête, une **seule** requête SQL vérifie que le compte
existe, qu'il est actif, que `token_version` correspond, et que la session `sid` est encore ouverte.

```python
select(User).where(User.id == user_id, User.is_active.is_(True),
                   User.token_version == token_version, session_is_active)
```

Une session coupée l'est donc **immédiatement**, pas après 15 minutes. Toute erreur donne un `401`
identique, avec `WWW-Authenticate: Bearer`.

Les routes de session sont sur un routeur qui déclare l'authentification **une seule fois** :

```python
session_router = APIRouter(prefix="/auth", dependencies=[Depends(get_current_user)])
```

## Étape 7 — Le renouvellement et la détection de vol

`POST /auth/refresh`, dans une seule transaction :

1. `SELECT … FOR UPDATE` sur l'empreinte du jeton reçu ;
2. jeton révoqué pour cause de rotation : c'est une **réutilisation**. Toute la session est
   révoquée, l'événement `refresh_token_reused` est journalisé, et la réponse est `401` ;
3. jeton expiré, session expirée ou compte inactif : `401`, sans couper la session ;
4. sinon : révocation du jeton (`rotated`), émission d'un nouveau dans la même session.

Pourquoi tout couper ? Un jeton déjà échangé ne revient que s'il est détenu par deux personnes,
dont un attaquant, et rien ne permet de savoir laquelle est légitime.

> **Rafraîchissements simultanés.** Grâce au verrou, deux renouvellements du même jeton passent
> l'un après l'autre : le second est traité comme une réutilisation. Le futur frontend devra
> donc sérialiser ses renouvellements.

## Étape 8 — La déconnexion

| Route | Effet |
|---|---|
| `POST /auth/logout` | Révoque la session du jeton utilisé, et elle seule |
| `POST /auth/logout-all` | Révoque toutes les sessions et incrémente `token_version` |

## Étape 9 — Tester

- jetons expirés, signés avec une autre clé, sans signature (`alg=none`), en HS512, modifiés ;
- chaque revendication manquante ou invalide ;
- les trois échecs de connexion identiques, avec une vérification argon2id chacun ;
- mise à niveau d'une empreinte aux paramètres faibles ;
- réutilisation : session coupée, jeton d'accès déjà émis refusé, événement journalisé ;
- deux renouvellements simultanés, sur deux connexions réelles : un seul réussit ;
- `logout` ne coupe que sa session, `logout-all` les coupe toutes ;
- routes de session : `401` sans jeton, avec un jeton expiré ou falsifié ;
- journaux sans mot de passe, sans jeton, sans clé et sans adresse e-mail.

## À retenir

- Un jeton prouve une identité **au moment de son émission** ; la base dit si elle vaut encore.
- Chaque échec d'authentification se ressemble : même message, même code, même temps.
- La limitation du débit de la connexion arrive à la phase 6 : l'API ne doit pas être exposée avant.

**Normes concernées :** RFC 7519 (JWT), RFC 8725 (bonnes pratiques JWT), RFC 6749 et 6750 ;
OWASP API Security Top 10 2023, API2 et API5 ; OWASP ASVS, chapitre V3 (sessions).
