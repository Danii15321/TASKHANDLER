# Phase 4 — Comptes et mots de passe

## Objectif

Créer des comptes en protégeant les mots de passe selon l'état de l'art.

**Résultat attendu :** aucun chemin de code ne peut renvoyer ou journaliser un mot de passe ou son
empreinte.

Le code complet et commenté se trouve dans `backend/app/`. Ce guide en explique les décisions.

## Étape 1 — Les dépendances

```bash
uv add argon2-cffi email-validator
```

- `argon2-cffi` : argon2id, vainqueur de la Password Hashing Competition et recommandé par l'OWASP.
  MD5, SHA-*, bcrypt et tout chiffrement réversible sont interdits pour les mots de passe.
- `email-validator` : la bibliothèque utilisée par `EmailStr` de Pydantic. On l'appelle toujours
  avec `check_deliverability=False` : aucune requête DNS ne part du serveur à chaque inscription.

## Étape 2 — La table `users` (`app/modules/users/models.py`)

| Colonne | Choix | Raison |
|---|---|---|
| `id` | UUID aléatoire (`gen_random_uuid()`) | Ne révèle ni le nombre de comptes, ni leur ordre |
| `email` | `VARCHAR(254)`, index unique sur `lower(email)` | `Alice@` et `alice@` ne peuvent pas coexister |
| `password_hash` | `CHECK (left(password_hash, 10) = '$argon2id$')` | Ni texte clair, ni bcrypt, même par erreur |
| `token_version`, `is_active` | Valeurs par défaut côté base | Utilisés par les sessions (phase 5) |

Chaque limite de validation est **doublée d'une contrainte en base** : si un jour un chemin de code
contourne la validation, PostgreSQL refuse la ligne.

Créez `app/db/models.py`, qui importe chaque modèle, et utilisez son `metadata` dans
`migrations/env.py` : un modèle oublié serait invisible pour `alembic check`.

## Étape 3 — La migration

Écrivez `migrations/versions/v0002_create_users.py` à la main, puis relisez-la. Piège rencontré :

```python
sa.CheckConstraint("token_version >= 0", name=op.f("ck_users_token_version_non_negative"))
```

Sans `op.f()`, la convention de nommage préfixe une seconde fois le nom
(`ck_users_ck_users_…`). `alembic check` ne compare pas les contraintes `CHECK` : seul un test
qui lit le nom réel de la contrainte violée l'a révélé.

Aucun nouveau `GRANT` : les privilèges par défaut de la migration 0001 donnent automatiquement au
rôle applicatif `SELECT`, `INSERT`, `UPDATE` et `DELETE` sur la nouvelle table, sans `TRUNCATE`.

## Étape 4 — Le hachage (`app/core/passwords.py`)

```python
PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=4, hash_len=32, salt_len=16,
               type=Type.ID)
```

Trois protections s'ajoutent à l'algorithme :

1. **Hors de la boucle d'événements.** Un hachage dure plusieurs dizaines de millisecondes ; dans
   la boucle, il bloquerait toutes les autres requêtes. `anyio.to_thread.run_sync` l'exécute dans
   un fil séparé.
2. **Nombre borné.** Chaque hachage réserve 64 Mio. Un `asyncio.Semaphore(4)` plafonne la mémoire à
   256 Mio, quel que soit le nombre de requêtes (OWASP API4).
3. **Attente bornée.** Au-delà de 10 secondes d'attente, la requête reçoit `503` avec
   `Retry-After`, au lieu de s'accumuler indéfiniment.

`abandon_on_cancel=False` : si le client se déconnecte, la place n'est rendue qu'à la fin réelle
du calcul. Sinon, des déconnexions répétées dépasseraient la borne.

Le sel est tiré à chaque appel, et la vérification compare à temps constant : ne réécrivez jamais
ces deux points vous-même.

## Étape 5 — La politique de mot de passe (`password_policy.py`)

Conforme au NIST SP 800-63B :

| Règle | Pourquoi |
|---|---|
| Normalisation NFKC | Une même saisie donne le même mot de passe, quel que soit le clavier |
| 12 à 128 caractères, comptés **après** normalisation | La normalisation peut allonger le texte |
| Aucune troncature | Chaque caractère saisi compte |
| Caractères de contrôle refusés | `\x00` ou `\n` n'ont rien à faire dans un mot de passe |
| Liste de 46 000 mots de passe courants | Les premiers essayés par un attaquant |
| Refus d'un mot de passe reprenant l'adresse | Ce sont les variantes testées en premier |
| **Aucune** règle de composition | Elles produisent `Motdepasse1!`, prévisible |

La connexion (phase 5) devra appliquer **exactement** la même normalisation avant de vérifier.

La liste vient de SecLists (licence MIT), à un commit figé. `scripts/build_common_passwords.py`
refuse toute source dont l'empreinte SHA-256 diffère, puis ne garde que les entrées de 12 à 128
caractères. Pour la régénérer :

```bash
curl -fsSL -o /tmp/source.txt "<SOURCE_URL indiquée dans le script>"
uv run --locked python scripts/build_common_passwords.py /tmp/source.txt
```

## Étape 6 — Les schémas (`schemas.py`)

`RegisterRequest` n'accepte que `email` et `password`, avec `extra="forbid"`, `strict=True` et
`hide_input_in_errors=True`. Le mot de passe porte `repr=False` : afficher le modèle ne le révèle
pas.

`UserRead` est une **liste blanche** : `id`, `email`, `created_at`. Un champ ajouté à la table
n'apparaît dans l'API que s'il est ajouté ici.

> **Sécurité : affectation de masse (OWASP API3).** Sans `extra="forbid"`, un client pourrait
> envoyer `"is_active": true` ou `"email_verified_at": …` et fixer lui-même une propriété du compte.

## Étape 7 — La route `POST /api/v1/auth/register`

Le service hache **avant** d'insérer : une adresse déjà inscrite prend le même temps qu'une
nouvelle. Le doublon est détecté par l'index unique (`IntegrityError`), jamais par une lecture
préalable : deux inscriptions simultanées ne peuvent pas passer toutes les deux.

| Cas | Réponse |
|---|---|
| Compte créé | `201`, `id`, `email`, `created_at` |
| Adresse déjà inscrite | `409`, message générique |
| Entrée invalide ou champ inconnu | `422`, sans recopier la valeur reçue |
| Hachages saturés | `503` et `Retry-After: 5` |

> **Limite temporaire.** Le `409` révèle qu'une adresse est inscrite. La phase 7 remplacera cette
> réponse par un `202` uniforme ; l'API ne doit pas être exposée publiquement d'ici là.

Les routes métier sont regroupées sous `/api/v1` (OWASP API9).

## Étape 8 — Tester

`isolated_client` remplace la session des routes par une session isolée : même les `commit` de
l'inscription sont annulés à la fin du test.

Vérifiez au minimum :

- empreinte au format `$argon2id$v=19$m=65536,t=3,p=4$`, sel différent à chaque appel ;
- calcul hors du fil de la boucle, jamais plus de calculs simultanés que la borne ;
- chaque règle de la politique, y compris la normalisation et l'allongement par NFKC ;
- chaque contrainte de la table, déclenchée directement en SQL ;
- réponse sans mot de passe ni empreinte, journaux sans l'un ni l'autre ;
- affectation de masse refusée pour chaque champ interne ;
- empreinte non argon2id refusée par la base, avec une erreur 500 générique.

## À retenir

- Le mot de passe ne vit que le temps de la requête : il n'est ni stocké, ni renvoyé, ni journalisé.
- La base est la dernière ligne de défense : elle refuse ce que l'application aurait dû refuser.
- Un test qui vérifie le **nom** de la contrainte violée prouve que c'est la bonne qui a agi.

**Normes concernées :** OWASP Password Storage Cheat Sheet ; NIST SP 800-63B ; OWASP API Security
Top 10 2023, API2, API3 et API4 ; OWASP ASVS, chapitre V2 (authentification).
