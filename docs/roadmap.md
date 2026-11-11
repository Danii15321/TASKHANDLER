# Roadmap TaskHandler

Vue d'ensemble des phases du backend. Le détail pédagogique de chaque phase est dans
`docs/steps/step-x.md`, où `x` est le numéro de la phase. Ce fichier est rédigé au démarrage de la
phase, pas avant.

## Règles d'avancement

- Une phase commence lorsque la précédente est terminée.
- Un outil, une dépendance ou un fichier de configuration n'est ajouté que par la phase qui en a
  besoin.
- Une case n'est cochée que lorsque le travail est écrit, testé et relu.
- Toute décision de conception structurante est consignée dans le guide de la phase concernée.
- Aucun commit n'est effectué sans autorisation explicite.

## Tableau de bord

| Phase | Sujet | État |
|---|---|---|
| 0 | Dépôt et outillage qualité | Terminée |
| 1 | Application FastAPI minimale | Terminée |
| 2 | PostgreSQL et migrations | Terminée |
| 3 | Intégration continue | Terminée |
| 4 | Comptes et mots de passe | Terminée |
| 5 | Authentification et sessions | En relecture |
| 6 | Protection contre les abus | À faire |
| 7 | Vérification d'e-mail et réinitialisation | À faire |
| 8 | Tâches | À faire |
| 9 | Grille hebdomadaire | À faire |
| 10 | Durcissement, RGPD et audit | À faire |
| 11 | Conteneurisation et déploiement | À faire |

---

## Phase 0 — Dépôt et outillage qualité

**Objectif :** un dépôt propre, où aucun secret ne peut entrer et où le code est vérifié dès la
première ligne.

- [x] Rédiger `docs/steps/step-0.md`
- [x] Initialiser Git et écrire le `.gitignore` (dont `.env` et ses variantes)
- [x] Initialiser `backend/` avec uv et épingler la version de Python
- [x] Configurer ruff, y compris les règles de sécurité Bandit
- [x] Configurer mypy en mode strict
- [x] Mettre en place pre-commit avec gitleaks
- [x] Ajouter pytest et un premier test qui valide la chaîne d'outils

**Terminé lorsque :** les hooks bloquent un faux secret et une erreur de type.

---

## Phase 1 — Application FastAPI minimale

**Objectif :** une application configurée, observable et sûre par défaut, sans logique métier.

- [x] Rédiger `docs/steps/step-1.md`
- [x] Installer FastAPI, Uvicorn et pydantic-settings
- [x] Créer la fabrique d'application `create_app()`
- [x] Charger une configuration validée, qui refuse de démarrer si elle est invalide
- [x] Fournir `.env.example` sans aucune valeur réelle
- [x] Mettre en place les journaux structurés et un identifiant par requête
- [x] Filtrer des journaux les mots de passe, jetons et en-têtes d'authentification
- [x] Unifier les erreurs au format RFC 9457, sans fuite d'information sur les erreurs 500
- [x] Exposer `GET /health/live`
- [x] Désactiver `/docs`, `/redoc` et `/openapi.json` en production
- [x] Tester la configuration, les erreurs et le mode production

**Terminé lorsque :** une erreur interne renvoie un message générique et un identifiant corrélé aux
journaux.

---

## Phase 2 — PostgreSQL et migrations

**Objectif :** une base versionnée, accessible avec le moindre privilège et testée en conditions
réelles.

- [x] Rédiger `docs/steps/step-2.md`
- [x] Ajouter PostgreSQL via Docker Compose pour le développement
- [x] Créer un rôle de migration (DDL) et un rôle applicatif (DML uniquement)
- [x] Installer SQLAlchemy asynchrone et asyncpg
- [x] Configurer moteur, pool de connexions et délais d'expiration des requêtes
- [x] Fournir la session par requête, avec annulation automatique en cas d'erreur
- [x] Définir la convention de nommage des contraintes
- [x] Initialiser Alembic avec le rôle de migration
- [x] Exposer `GET /health/ready`, sans détail sur l'infrastructure
- [x] Créer l'infrastructure de test sur un vrai PostgreSQL, isolée à chaque test
- [x] Vérifier que le rôle applicatif ne peut pas modifier le schéma

**Terminé lorsque :** une migration s'applique puis s'annule proprement, et un test prouve que le
rôle applicatif ne peut pas exécuter `DROP TABLE`.

---

## Phase 3 — Intégration continue

**Objectif :** aucun changement n'est accepté sans passer les contrôles de qualité et de sécurité.

- [x] Rédiger `docs/steps/step-3.md`
- [x] Créer le pipeline CI avec un service PostgreSQL
- [x] Exécuter ruff, mypy et pytest
- [x] Contrôler que les migrations reflètent les modèles (`alembic check`)
- [x] Ajouter pip-audit et gitleaks sur tout l'historique
- [x] Épingler les actions CI par empreinte et restreindre les permissions du pipeline
- [x] Mesurer la couverture de tests et fixer un seuil
- [x] Travailler sur `daniel_dev` et protéger `main` : pull request et CI verte obligatoires

**Terminé lorsque :** une dépendance vulnérable ou un test en échec bloque le pipeline.

---

## Phase 4 — Comptes et mots de passe

**Objectif :** créer des comptes en protégeant les mots de passe selon l'état de l'art.

- [x] Rédiger `docs/steps/step-4.md`
- [x] Créer la table `users` et sa migration
- [x] Normaliser les e-mails et garantir leur unicité en base
- [x] Implémenter le hachage argon2id hors de la boucle événementielle
- [x] Borner les hachages simultanés pour éviter l'épuisement de la mémoire
- [x] Appliquer la politique de mot de passe : 12 à 128 caractères, liste de mots de passe courants
- [x] Implémenter `POST /auth/register`
- [x] Séparer strictement les schémas d'entrée et de sortie
- [x] Tester le hachage, la politique et l'absence du hash dans les réponses

**Terminé lorsque :** aucun chemin de code ne peut renvoyer ou journaliser un mot de passe ou son
empreinte.

> **Limite temporaire :** jusqu'à la phase 7, l'inscription révèle si une adresse existe déjà. La
> phase 7 corrige ce point ; l'API ne doit pas être exposée publiquement avant.

---

## Phase 5 — Authentification et sessions

**Objectif :** des sessions courtes, révocables, et résistantes au vol de jeton.

- [ ] Rédiger `docs/steps/step-5.md`
- [ ] Installer PyJWT et valider une clé de signature d'au moins 256 bits
- [ ] Implémenter `POST /auth/login` avec une réponse identique et un temps constant, compte existant ou non
- [ ] Émettre un jeton d'accès de 15 minutes avec `iss`, `aud`, `sub`, `iat`, `exp`, `jti`
- [ ] Vérifier strictement algorithme, signature et revendications
- [ ] Créer la dépendance `get_current_user`, qui recharge l'utilisateur à chaque requête
- [ ] Stocker les jetons de renouvellement opaques sous forme hachée
- [ ] Implémenter `POST /auth/refresh` avec rotation atomique
- [ ] Révoquer toute la famille de jetons en cas de réutilisation
- [ ] Implémenter `POST /auth/logout` et `POST /auth/logout-all`
- [ ] Invalider les jetons d'accès via `token_version`
- [ ] Mettre à niveau les empreintes lorsque les paramètres argon2id changent
- [ ] Tester jetons expirés, falsifiés, `alg=none`, réutilisés et rafraîchissements concurrents

**Terminé lorsque :** la réutilisation d'un jeton de renouvellement coupe toute la session associée.

---

## Phase 6 — Protection contre les abus

**Objectif :** rendre les attaques automatisées coûteuses, sans permettre de bloquer un compte
légitime.

- [ ] Rédiger `docs/steps/step-6.md`
- [ ] Ajouter Redis, protégé par mot de passe et non exposé
- [ ] Implémenter un limiteur de débit à compteurs atomiques
- [ ] Limiter connexion, inscription et réinitialisation par IP et par compte
- [ ] Appliquer une temporisation progressive plutôt qu'un verrouillage définitif
- [ ] Rejeter les requêtes d'authentification si Redis est indisponible
- [ ] N'accepter `X-Forwarded-For` que depuis des proxys de confiance
- [ ] Limiter la taille des corps de requête
- [ ] Répondre `429` avec `Retry-After`
- [ ] Tester le dépassement des limites, le contournement et la panne de Redis

**Terminé lorsque :** un test de force brute est bloqué sans verrouiller durablement la victime.

---

## Phase 7 — Vérification d'e-mail et réinitialisation

**Objectif :** des parcours de compte qui ne révèlent pas quelles adresses sont inscrites.

- [ ] Rédiger `docs/steps/step-7.md`
- [ ] Créer une interface d'envoi d'e-mails, avec Mailpit en développement
- [ ] Créer la table `one_time_tokens`, avec jetons hachés, expirants et à usage unique
- [ ] Rendre `POST /auth/register` uniforme (`202`), que l'adresse existe ou non
- [ ] Implémenter `POST /auth/verify-email`
- [ ] Refuser la connexion tant que l'adresse n'est pas vérifiée
- [ ] Implémenter `POST /auth/password/forgot` avec réponse uniforme
- [ ] Implémenter `POST /auth/password/reset`, qui révoque toutes les sessions
- [ ] Tester l'expiration, la réutilisation et l'uniformité des réponses

**Terminé lorsque :** les réponses et les temps de traitement ne permettent pas de distinguer une
adresse connue d'une adresse inconnue.

---

## Phase 8 — Tâches

**Objectif :** un CRUD complet où un utilisateur ne peut jamais atteindre les données d'un autre.

- [ ] Rédiger `docs/steps/step-8.md`
- [ ] Créer la table `tasks`, avec titre et description obligatoires et contraintes `CHECK`
- [ ] Créer la table d'association N-N `task_days` : `task_id`, `user_id`, `day`, `start_hour`, `duration_hours`
- [ ] Garantir par clé étrangère composite (`task_id`, `user_id`) qu'un jour appartient au propriétaire de la tâche
- [ ] Garantir en base qu'une tâche tient dans la journée : début de 0 à 23, durée d'au moins 1, fin à 24 au plus
- [ ] Définir les schémas : titre de 1 à 200 caractères, description de 1 à 5 000
- [ ] Rejeter les champs vides, inconnus et les caractères de contrôle, dont l'octet NUL
- [ ] Filtrer systématiquement chaque requête par propriétaire dans le dépôt
- [ ] Implémenter création, lecture, modification partielle et suppression
- [ ] Exiger au moins un jour à la création, et 31 jours au plus par requête
- [ ] Implémenter `PUT` et `DELETE /tasks/{id}/days/{day}`
- [ ] Refuser le retrait du dernier jour d'une tâche, sous verrou de ligne
- [ ] Répondre `404` pour la ressource d'un autre utilisateur
- [ ] Tester l'isolation entre utilisateurs sur chaque route

**Terminé lorsque :** chaque route a un test prouvant qu'un second utilisateur reçoit `404`.

---

## Phase 9 — Grille hebdomadaire

**Objectif :** fournir à la grille des données légères et fiables, sans aucun chevauchement.

- [ ] Rédiger `docs/steps/step-9.md`
- [ ] Interdire en base le chevauchement de deux tâches d'un utilisateur (`EXCLUDE`, extension `btree_gist`)
- [ ] Traduire ce conflit en réponse `409`, y compris entre requêtes concurrentes
- [ ] Implémenter `GET /planning?start=…&end=…`, avec une période de 31 jours au plus
- [ ] Renvoyer dans la grille le titre et la position des tâches, jamais leur description
- [ ] Valider les jours au format ISO 8601 et refuser les périodes incohérentes
- [ ] Ajouter par migration le statut et `completed_at` à `task_days`, et la priorité à `tasks`
- [ ] Définir les transitions de statut et la gestion de `completed_at`
- [ ] Ajouter les filtres de la grille par statut et priorité
- [ ] Implémenter une pagination par curseur sur `GET /tasks`, avec taille de page maximale
- [ ] Créer les index composites à partir des requêtes réelles
- [ ] Tester les chevauchements, les bornes, les filtres et l'isolation de la grille

**Terminé lorsque :** deux requêtes simultanées ne peuvent pas occuper la même case, et la grille ne
renvoie aucune description.

---

## Phase 10 — Durcissement, RGPD et audit

**Objectif :** vérifier l'ensemble de l'API face à un référentiel, et couvrir le cycle de vie des
données.

- [ ] Rédiger `docs/steps/step-10.md`
- [ ] Implémenter `POST /users/me/password` avec réauthentification
- [ ] Implémenter `GET /users/me`, puis `DELETE /users/me` avec réauthentification
- [ ] Implémenter `GET /users/me/export`
- [ ] Définir et appliquer la durée de conservation, puis purger les jetons expirés
- [ ] Ajouter les en-têtes de sécurité, `TrustedHostMiddleware` et une liste CORS explicite
- [ ] Journaliser les événements d'audit sans donnée sensible
- [ ] Évaluer la sécurité au niveau des lignes de PostgreSQL (RLS) et consigner la décision
- [ ] Parcourir l'OWASP API Security Top 10 point par point
- [ ] Vérifier les exigences applicables de l'OWASP ASVS
- [ ] Lancer un scan dynamique avec OWASP ZAP sur l'API locale
- [ ] Corriger chaque constat ou documenter son acceptation

**Terminé lorsque :** chaque point du Top 10 a une preuve : test, configuration ou décision écrite.

---

## Phase 11 — Conteneurisation et déploiement

**Objectif :** un déploiement reproductible, minimal et restaurable.

- [ ] Rédiger `docs/steps/step-11.md`
- [ ] Créer un `Dockerfile` multi-étapes, utilisateur non root, image épinglée par empreinte
- [ ] Exécuter les migrations dans une étape séparée, avec le rôle dédié
- [ ] Placer TLS et les limites de requêtes dans un proxy inverse
- [ ] Imposer HSTS en production et TLS vers PostgreSQL
- [ ] Injecter les secrets par l'environnement d'exécution et documenter leur rotation
- [ ] Générer un SBOM et analyser l'image dans la CI
- [ ] Automatiser des sauvegardes chiffrées
- [ ] Réaliser et chronométrer un test de restauration
- [ ] Rédiger le plan de réponse à incident
- [ ] Compléter la section Installation du README

**Terminé lorsque :** une restauration complète a été réalisée à partir d'une sauvegarde.

---

## Après la version 1

Sujets à réévaluer une fois le backend stabilisé :

- [ ] Frontend, avec jeton de renouvellement en cookie `HttpOnly`, `Secure`, `SameSite` et protection CSRF
- [ ] Authentification à deux facteurs (TOTP, WebAuthn)
- [ ] Tâches récurrentes
- [ ] Notifications et rappels
