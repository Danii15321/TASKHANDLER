# Phase 11 — Conteneurisation et déploiement

## Objectif

Un déploiement reproductible, minimal et restaurable.

**Résultat attendu :** une restauration complète a été réalisée à partir d'une sauvegarde chiffrée,
et chronométrée.

Fichiers : `backend/Dockerfile`, `backend/.dockerignore`, `deploy/` (compose, nginx, PostgreSQL,
Redis, sauvegardes, scripts), `app/core/tls.py`, et le job `image` de la CI.

## Décisions de la phase

| Sujet | Choix | Raison |
|---|---|---|
| Cible | Pile de production reproduite en local | Restauration réelle, sans dépendre d'un hébergeur |
| Proxy | nginx non root (`nginx-unprivileged`) | Limites de débit et de taille natives |
| Secrets | Fichiers montés dans `/run/secrets` | Invisibles dans `docker inspect` et `/proc/<pid>/environ` |
| Sauvegardes | `pg_dump` chiffré par `age` vers une clé publique | Un serveur compromis ne peut pas relire ses sauvegardes |
| SBOM et analyse | Syft (CycloneDX) puis Grype | Outils dédiés, images épinglées, sans action tierce |
| Correctifs Debian | `apt-get upgrade` à la construction | Correctif disponible sans attendre l'image officielle |

## Étape 1 — L'image de l'API

`backend/Dockerfile` en deux étapes. La première installe les dépendances avec uv (`--locked
--no-dev`) ; la seconde ne recopie que l'environnement et le code.

- Images de base **épinglées par empreinte** ; une mise à jour est un changement relu.
- Utilisateur `taskhandler` (UID 10001), sans shell ni dossier personnel ; le code appartient à
  root : un processus compromis ne peut pas le réécrire.
- `pip` supprimé, bytecode compilé à la construction (le conteneur tourne en lecture seule).
- `.dockerignore` en **liste autorisée** : un `.env` ou une clé ne peuvent pas entrer dans l'image.
- Résultat : 67 Mio, aucun outil de développement, aucun fichier `.env`.

> **Piège rencontré.** Grype a signalé deux CVE hautes dans `zlib1g`, déjà corrigées par Debian
> mais pas encore dans l'image Python officielle. D'où `apt-get upgrade` dans l'étape finale.

## Étape 2 — Secrets en fichiers

`Settings` et `MigrationSettings` lisent `secrets_dir=/run/secrets`, seulement si le dossier
existe (pas d'avertissement en développement). Un fichier `taskhandler_jwt_secret` remplace la
variable `TASKHANDLER_JWT_SECRET` ; la validation est la même (secret trop court : refus).
Chaque conteneur ne reçoit **que ses** secrets : la migration n'a jamais celui de l'API, et
inversement.

## Étape 3 — TLS partout, vérifié

`scripts/init-local.sh` crée une autorité interne (ECDSA P-256) et un certificat par service,
valable pour son seul nom d'hôte (`subjectAltName`). `client_tls_context` exige le certificat et
le nom d'hôte, TLS 1.2 au moins, et **ajoute** l'autorité interne à celles du système (un
fournisseur SMTP public reste vérifiable).

| Lien | Protection | Vérifié |
|---|---|---|
| Client → nginx | TLS 1.2/1.3, profil Mozilla intermédiaire, SNI inconnu refusé | TLS 1.1 refusé, poignée de main refusée sans le bon nom |
| API → PostgreSQL | `ssl=verify-full`, `pg_hba` en `hostssl` seul | Connexion en clair rejetée, nom d'hôte faux rejeté, API en TLS 1.3 |
| API → Redis | `rediss://`, port en clair fermé | Port 6379 fermé, 6380 refuse le clair |
| API → SMTP | TLS implicite et authentification | E-mail reçu par Mailpit en TLS |

> **Piège rencontré.** SQLAlchemy passe les options à asyncpg une par une : `sslrootcert` ne
> peut pas passer par l'URL. Le contexte TLS est donc fourni dans `connect_args`.

## Étape 4 — La pile de production (`deploy/compose.prod.yaml`)

- **Réseaux** : `public` (nginx seul, seul port publié, sur 127.0.0.1) ; `edge` interne (nginx et
  API) ; `data` interne (API, PostgreSQL, Redis, SMTP). L'API n'a **aucun accès à Internet**
  (vérifié : « Network is unreachable »), ce qui ferme la porte au SSRF et à l'exfiltration.
- **Durcissement commun** : `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, limites de
  mémoire et de processus. PostgreSQL ne récupère que les capacités de son changement
  d'utilisateur.
- **Migration séparée** : le service `migrate` applique `alembic upgrade head` avec le rôle de
  migration, puis s'arrête ; l'API ne démarre qu'après son succès.
- `X-Forwarded-For` est **remplacé** par nginx, et l'API ne le croit que venant du réseau `edge`.

> **Piège rencontré.** Un port publié sur un réseau `internal` ne fonctionne pas : l'interface de
> Mailpit se lit donc depuis le conteneur, et le réseau `data` reste entièrement interne.

## Étape 5 — nginx

Corps limité à 1 Mio, délais contre les connexions lentes, 20 requêtes par seconde et par IP
(rafale de 40), 20 connexions simultanées. Les erreurs produites par nginx (413, 429, 503) sont au
format RFC 9457, avec les en-têtes de sécurité. HSTS reste porté par l'API (phase 10) ; nginx
l'ajoute seulement à ses propres erreurs.

## Étape 6 — Sauvegardes chiffrées

```text
pg_dump -Fc (rôle taskhandler_backup, TLS) | age -r <clé publique> > fichier.partial → renommage
```

- Rôle **en lecture seule** (`pg_read_all_data`) avec `BYPASSRLS` : sans lui, la RLS cacherait
  les tâches et la sauvegarde serait incomplète.
- Le dump en clair ne touche jamais le disque ; un fichier partiel est supprimé en cas d'échec.
- `backup-scheduler` sauvegarde une fois par jour ; conservation de 14 jours (RGPD).

## Étape 7 — Restauration chronométrée

`scripts/restore-check.sh` restaure la dernière sauvegarde dans une pile **isolée** (autre projet,
autres sous-réseaux), en une transaction, puis vérifie la migration, `/health/ready` et les
données. Le script refuse de viser la pile de production.

| Mesure (11/11/2026) | Résultat |
|---|---|
| Restauration (déchiffrement, restauration, démarrage, vérifications) | **34 s** |
| Répétition complète depuis zéro (secrets, pile, données, sauvegarde, restauration) | 133 s |
| Après restauration | Connexion du compte d'avant la sauvegarde, ses 2 tâches, RLS, droits et version 0007 intacts |

## Étape 8 — SBOM et analyse dans la CI

Job `image` : construction, contrôle de l'utilisateur non root, export en archive (les outils
n'ont jamais le socket Docker), SBOM CycloneDX par Syft (gardé 90 jours), analyse par Grype :
**échec sur toute vulnérabilité haute ou critique corrigeable**. Restent, sous ce seuil, trois CVE
de l'interpréteur Python corrigées seulement dans des versions futures.

## Étape 9 — Rotation et incidents

`docs/exploitation.md` décrit la rotation de chaque secret ; celles de la clé JWT (ancien jeton
refusé, sessions conservées) et du mot de passe applicatif ont été exécutées.
`docs/incident-response.md` suit le NIST SP 800-61 et les articles 33 et 34 du RGPD (72 heures).

## Bonnes pratiques et normes de la phase

- OWASP Docker Security Cheat Sheet, CIS Docker Benchmark : non root, lecture seule, capacités
  retirées, images épinglées, aucun secret dans l'image.
- Mozilla Server Side TLS (intermédiaire), RFC 8996 (TLS 1.0 et 1.1 retirés).
- Règle 3-2-1 des sauvegardes ; une sauvegarde n'existe que si sa restauration a été testée.
- RGPD art. 32 (chiffrement, restauration), 33 et 34 (notification des violations).
- SBOM CycloneDX : savoir ce qui tourne, pour réagir vite à une nouvelle CVE.

## À retenir

Le required check `image` doit être ajouté à la règle de protection de `main` sur GitHub. La clé
privée de sauvegarde et la clé de l'autorité ne doivent jamais rester sur un vrai serveur.
