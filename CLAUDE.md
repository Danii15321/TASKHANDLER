# CLAUDE.md

TaskHandler est une API FastAPI + PostgreSQL de planification de tâches dans une grille hebdomadaire.
Elle stocke des données personnelles : **la sécurité prime sur la rapidité de livraison**.

Lire [README.md](README.md) pour le projet et [docs/roadmap.md](docs/roadmap.md) pour l'avancement.
Avant de coder, lire le fichier `docs/steps/step-x.md` de la phase en cours.

## Règles de travail (non négociables)

- **Jamais de commit, de push ni de `git init` sans autorisation explicite**, demandée à chaque fois.
- **Aucun scaffold global.** Un fichier, un dossier, une dépendance ou un service (Docker, Redis,
  CI…) n'est créé que si la phase en cours l'exige.
- Rester dans le périmètre de la phase. Signaler ce qui en sort au lieu de le faire.
- Cocher une case de la roadmap seulement si le travail est écrit, testé et relu.
- Zéro compromis sur la qualité : pas de `TODO` de sécurité, de contournement ou de test désactivé.
- Face à un choix de conception ayant un impact sur la sécurité, présenter le compromis et demander.
- Ajouter une commande au README seulement après l'avoir réellement exécutée.
- Tailles maximales : `README.md`, `CLAUDE.md` et `docs/steps/step-x.md` 200 lignes,
  `docs/roadmap.md` 500 lignes.
- Chaque `step-x.md` est pédagogique : un humain doit pouvoir écrire le code en le suivant. Il
  mentionne explicitement les bonnes pratiques et les normes de sécurité de la phase.
- Rédiger le code, les commentaires et la documentation en français ; les identifiants en anglais.

## Format obligatoire du code

Toute fonction, méthode ou fixture, y compris privée et de test, est **précédée** de ces trois
étiquettes, en commentaires, chacune avec une courte description en français :

```python
# requires : `plain` contient entre 12 et 128 caractères.
# assigns  : rien (aucun état global ni écriture en base).
# ensures  : renvoie une empreinte argon2id au format PHC, avec un sel propre à cet appel.
async def hash_password(plain: str) -> str:
    # Le hachage est coûteux : il s'exécute hors de la boucle événementielle
    # pour ne pas bloquer les autres requêtes.
    return await run_in_threadpool(_hasher.hash, plain)
```

- `requires` : préconditions sur les arguments et l'état.
- `assigns` : tout ce qui est modifié (base, cache, variables, fichiers, envoi d'e-mail), sinon `rien`.
- `ensures` : postconditions, valeur renvoyée et exceptions possibles.
- Le code est abondamment commenté : expliquer le **pourquoi**, surtout pour les choix de sécurité.
- Typage complet (`mypy --strict`). `# type: ignore` et `# noqa` exigent une justification en ligne.

## Modèle de menace

| OWASP API 2023 | Risque ici | Contrôle attendu |
|---|---|---|
| API1 — Accès aux objets | Lire la tâche d'un autre utilisateur | Filtre par propriétaire dans chaque requête |
| API2 — Authentification | Force brute, vol de jeton | argon2id, rotation, limitation de débit |
| API3 — Propriétés d'objets | Modifier `user_id`, exposer un hash | Schémas d'entrée et de sortie distincts |
| API4 — Consommation | Corps géants, argon2 saturé | Tailles, pages et hachages simultanés bornés |
| API5 — Fonctions | Route sensible sans protection | Authentification par défaut sur le routeur |
| API6 — Flux métier | Inscriptions et e-mails de masse | Limites par IP et par compte |
| API7 — SSRF | Récupération d'URL fournie | Aucune requête sortante vers une URL utilisateur |
| API8 — Configuration | Mode debug, CORS ouvert | Configuration validée, sûre par défaut |
| API9 — Inventaire | Routes oubliées | Versionnement `/api/v1`, contrat OpenAPI testé |
| API10 — Services tiers | Réponse SMTP ou Redis altérée | Données externes traitées comme non fiables |

## Mots de passe

- argon2id via `argon2-cffi`, paramètres minimum `m=65536`, `t=3`, `p=4`. MD5, SHA-*, bcrypt ou
  chiffrement réversible sont interdits pour les mots de passe.
- Toujours hacher dans un thread séparé, avec un sémaphore bornant les hachages simultanés.
- Longueur de 12 à 128 caractères, sans troncature silencieuse. Refuser les mots de passe courants.
  Ne pas imposer de règles de composition.
- À la connexion, si le compte n'existe pas, vérifier une empreinte factice : le temps de réponse ne
  doit pas révéler l'existence du compte. Réhacher si `check_needs_rehash` le demande.
- Changement de mot de passe, suppression de compte : réauthentification obligatoire.
- Réinitialisation ou changement du mot de passe : révoquer toutes les sessions.
- Réponse d'échec unique : `identifiants invalides`, sans distinguer l'e-mail du mot de passe.

## Jetons et sessions

- Jeton d'accès : JWT via PyJWT (jamais `python-jose`), 15 minutes, signé avec un secret ≥ 256 bits.
- Décodage avec `algorithms=["HS256"]` en liste fixe, et `iss`, `aud`, `exp`, `iat`, `sub` exigés.
  Ne jamais lire l'algorithme depuis l'en-tête du jeton, ni désactiver une vérification.
- Aucune donnée personnelle dans un JWT : `sub` contient l'UUID, rien de plus.
- `get_current_user` recharge l'utilisateur en base, rejette tout compte inactif et compare
  `token_version`.
- Jeton de renouvellement : `secrets.token_urlsafe(32)`, opaque, stocké uniquement en SHA-256.
- Rotation à chaque usage, dans une transaction avec `SELECT … FOR UPDATE`. Un jeton déjà utilisé
  révoque toute sa famille.
- Jetons à usage unique (e-mail, réinitialisation) : aléatoires, hachés, expirants, consommés
  atomiquement ; 30 minutes maximum pour la réinitialisation.
- Comparaison de secrets avec `hmac.compare_digest`. `random` est interdit pour la sécurité.

## Autorisation et isolation

- **Refus par défaut** : les routeurs métier déclarent l'authentification au niveau du routeur.
  Seules les routes listées publiques dans le README peuvent s'en passer.
- Toute requête sur une donnée utilisateur inclut `user_id` dans le `WHERE` du dépôt, y compris
  `UPDATE` et `DELETE`. Pas de lecture suivie d'une vérification en Python.
- `user_id` provient uniquement du jeton, jamais du corps, de la route ou d'un paramètre.
- La ressource d'un autre utilisateur répond `404`, jamais `403` : ne pas révéler son existence.
- Pour une ressource enfant (jour d'une tâche), vérifier le propriétaire via le parent, dans la même
  requête.

## Validation des entrées et des sorties

- Schémas Pydantic avec `extra="forbid"` ; tous les champs sont bornés (longueur, intervalle, format).
- Schémas distincts `Create`, `Update` et `Read`. Ne jamais accepter ni renvoyer un modèle ORM.
- `response_model` explicite sur chaque route : `password_hash` ne doit sortir d'aucun schéma.
- Rejeter l'octet NUL et les caractères de contrôle, ainsi que les champs vides après nettoyage.
- Horodatages : fuseau horaire obligatoire, stockage UTC. Jours : ISO 8601, stockés en `DATE`.
- Pagination bornée (100 éléments maximum). Tri et filtres limités à une liste de champs autorisés.
- Période de la grille limitée à 31 jours. La grille ne renvoie jamais la description des tâches.
- Toute limite de validation importante est doublée d'une contrainte en base (`CHECK`, `UNIQUE`,
  `NOT NULL`, clé étrangère).

## Base de données

- SQLAlchemy avec paramètres liés. Interdit : f-string, `%` ou concaténation pour construire du SQL.
- `Base.metadata.create_all` est interdit : tout changement passe par une migration Alembic relue.
- Deux rôles : migration (DDL) et application (DML seulement). Jamais de superutilisateur.
- Chaque migration a un `downgrade` testé et ne contient ni secret ni donnée personnelle.
- Délais imposés (`statement_timeout`, pool) ; TLS vérifié vers PostgreSQL en production.
- Tests sur un vrai PostgreSQL : SQLite est interdit, y compris dans les tests.
- Ne jamais exécuter `downgrade`, `DROP`, `TRUNCATE` ou un `DELETE` massif sans autorisation.

## Protection contre les abus

- Limites indicatives par fenêtre glissante, ajustables dans la configuration :

| Route | Limite |
|---|---|
| Connexion | 5 par minute et par couple IP + compte, 20 par minute et par IP |
| Inscription, mot de passe oublié | 5 par heure et par IP, 3 par heure et par adresse |
| Renouvellement | 30 par minute et par utilisateur |
| Routes authentifiées | 120 par minute et par utilisateur |

- Routes d'authentification : refuser si Redis est indisponible. Temporisation progressive, jamais
  de verrouillage définitif, qui permettrait de bloquer un compte à distance.
- Inscription et mot de passe oublié répondent de façon uniforme (`202`), adresse connue ou non.
- `X-Forwarded-For` n'est lu que depuis une liste explicite de proxys de confiance.
- Corps de requête limités (1 Mio par défaut).

## Configuration et secrets

- Secrets uniquement dans l'environnement, chargés par pydantic-settings et typés `SecretStr`.
- Pas de valeur par défaut pour un secret. L'application refuse de démarrer si un secret manque, est
  trop court ou correspond à une valeur d'exemple.
- `.env.example` ne contient que des valeurs factices. `.env` n'est jamais versionné.
- Claude ne lit ni n'affiche jamais `.env`, un secret réel ou une clé privée. S'il en rencontre un
  dans le code ou une sortie : le masquer, arrêter et prévenir. Ce secret doit être renouvelé.
- En production : `debug=False`, `/docs`, `/redoc` et `/openapi.json` désactivés.

## Erreurs et journaux

- Réponses `application/problem+json`. Une erreur 500 renvoie un message générique et l'identifiant
  de requête : ni trace, ni SQL, ni nom de table, ni version.
- Ne jamais journaliser : mot de passe, empreinte, jeton, en-tête `Authorization`, cookie, corps de
  requête, description de tâche. Filtrage automatique dans le formateur de journaux.
- Journaliser les événements d'audit : connexion réussie ou échouée, réutilisation de jeton,
  changement de mot de passe, suppression de compte, limite atteinte.
- Neutraliser les retours à la ligne dans les valeurs contrôlées par l'utilisateur.

## HTTP

- CORS : liste explicite d'origines, jamais `*`. `TrustedHostMiddleware` actif.
- En-têtes : `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` sur les réponses
  authentifiées, `Content-Security-Policy: default-src 'none'`, HSTS en production.
- Jetons uniquement dans l'en-tête `Authorization` ou le corps, jamais dans une URL pour une route API.
- `POST`, `PATCH`, `PUT` et `DELETE` pour toute modification : un `GET` n'a jamais d'effet de bord.

## Dépendances

- Pas de nouvelle dépendance sans justification et validation. Vérifier le nom exact (typosquatting),
  la maintenance et les vulnérabilités connues.
- Versions verrouillées dans `uv.lock`. `pip-audit` doit passer avant de terminer un travail.
- Pas de `eval`, `exec`, `pickle` sur des données non fiables, ni de `subprocess` avec `shell=True`.

## Tests de sécurité obligatoires

Toute route protégée dispose au minimum des tests suivants :

- sans jeton, avec un jeton expiré et avec un jeton falsifié : `401` ;
- ressource d'un autre utilisateur, en lecture, modification et suppression : `404`, données intactes ;
- entrée invalide, champ inconnu ou `user_id` injecté : `422` ;
- réponse sans aucun champ sensible.

Toute correction d'un défaut de sécurité commence par un test qui le reproduit.

## Avant de déclarer un travail terminé

1. Les contrôles disponibles à cette phase passent : ruff, mypy, pytest, pip-audit, gitleaks.
2. Chaque nouvelle fonction porte `requires`, `assigns` et `ensures`.
3. Chaque nouvelle route est authentifiée, ou explicitement publique, avec ses tests de sécurité.
4. Chaque modification de schéma passe par une migration, appliquée puis annulée.
5. Aucun secret ni aucune donnée sensible dans le code, les journaux ou les réponses.
6. La roadmap est à jour et les limites de taille des fichiers sont respectées.
7. Indiquer ce qui a été vérifié, ce qui ne l'a pas été, puis **demander avant tout commit**.
