# Roadmap du frontend TaskHandler

Vue d'ensemble des phases du frontend. Le détail pédagogique de chaque phase est dans
`frontend/docs/steps/step-x.md`, où `x` est le numéro de la phase. Ce fichier est rédigé au
démarrage de la phase, pas avant. Les règles propres au frontend sont dans
[frontend/CLAUDE.md](CLAUDE.md), en complément du [CLAUDE.md](../CLAUDE.md) racine.

## Décisions structurantes

| Sujet | Décision | Conséquence |
|---|---|---|
| Framework | Next.js (App Router) + React + TypeScript `strict` | Typage de bout en bout, routage par fichiers |
| Dépendances | pnpm, version épinglée, `pnpm-lock.yaml` versionné | Poetry est exclu : il ne gère que des paquets Python |
| Appels à l'API | Le navigateur appelle directement l'API `/api/v1` | Le serveur Next.js ne voit jamais un jeton ni une donnée utilisateur |
| Jeton d'accès | En mémoire JavaScript uniquement | Jamais dans `localStorage`, `sessionStorage`, IndexedDB, un cookie lisible ou une URL |
| Jeton de renouvellement | Cookie `HttpOnly`, `Secure`, `SameSite=Strict`, posé par l'API | Exige une adaptation du backend (phase 3) et une protection CSRF |
| Rendu | Pages authentifiées en composants client | Les composants serveur ne rendent que des contenus publics |
| Écritures | Uniquement via l'API, jamais par Server Action ni Route Handler | Surface d'attaque côté serveur Next.js minimale |
| Origines | Frontend et API sur le même site (ex. `app.` et `api.taskhandler…`) | `SameSite=Strict` fonctionne sans cookie tiers |

Le compromis retenu : le jeton d'accès, bref (15 minutes), reste lisible par un script en cas de
XSS. La défense repose donc sur une CSP stricte à nonce, l'absence totale d'injection de HTML et
le jeton de renouvellement hors de portée de JavaScript.

## Règles d'avancement

- Une phase commence lorsque la précédente est terminée.
- Un outil, une dépendance ou un fichier de configuration n'est ajouté que par la phase qui en a
  besoin.
- Une case n'est cochée que lorsque le travail est écrit, testé et relu.
- Toute décision de conception structurante est consignée dans le guide de la phase concernée.
- Une phase qui modifie le backend en respecte toutes les règles (migrations, tests de sécurité,
  étiquettes `requires`, `assigns`, `ensures`).
- Aucun commit n'est effectué sans autorisation explicite.

## Tableau de bord

| Phase | Sujet | État |
|---|---|---|
| 0 | Socle Next.js et outillage qualité | Terminée |
| 1 | Application minimale sûre par défaut | Terminée |
| 2 | Intégration continue du frontend | En relecture |
| 3 | Adaptation de l'API au navigateur | Terminée |
| 4 | Client API et gestion de session | À faire |
| 5 | Parcours de compte | À faire |
| 6 | Tâches | À faire |
| 7 | Grille hebdomadaire | À faire |
| 8 | Compte et RGPD | À faire |
| 9 | Accessibilité, tests de bout en bout et audit | À faire |
| 10 | Conteneurisation et déploiement | À faire |

---

## Phase 0 — Socle Next.js et outillage qualité

**Objectif :** un projet frontend reproductible, où une dépendance douteuse, une erreur de type ou un
secret sont bloqués dès la première ligne.

- [x] Rédiger `frontend/docs/steps/step-0.md`
- [x] Épingler la version LTS de Node.js et celle de pnpm (`packageManager` dans `package.json`)
- [x] Initialiser Next.js sans gabarit superflu, et compléter le `.gitignore` (`node_modules/`, `.next/`)
- [x] Configurer pnpm : scripts d'installation bloqués sauf liste blanche, délai minimal avant
      d'adopter une version fraîchement publiée, installation `--frozen-lockfile`
- [x] Configurer TypeScript en mode `strict`, avec `noUncheckedIndexedAccess`
- [x] Configurer ESLint (règles Next.js, React, accessibilité et sécurité) et le formatage
- [x] Désactiver la télémétrie de Next.js
- [x] Ajouter les hooks pre-commit du frontend (lint, types), à côté de gitleaks
- [x] Ajouter Vitest et un premier test qui valide la chaîne d'outils

**Terminé lorsque :** les hooks bloquent une erreur de type, une règle ESLint violée et un faux secret.

---

## Phase 1 — Application minimale sûre par défaut

**Objectif :** une coquille d'application sans logique métier, déjà protégée par ses en-têtes.

- [x] Rédiger `frontend/docs/steps/step-1.md`
- [x] Valider au démarrage la configuration publique (adresse de l'API en HTTPS hors développement)
- [x] Vérifier qu'aucun secret ne transite par une variable `NEXT_PUBLIC_*`
- [x] Générer une CSP stricte à nonce par requête : `default-src 'none'`, `script-src` à nonce et
      `'strict-dynamic'`, `connect-src` limité à l'API, `object-src`, `base-uri` et `frame-ancestors` à `'none'`
- [x] Ajouter `Referrer-Policy: no-referrer`, `X-Content-Type-Options`, `Permissions-Policy`,
      `poweredByHeader: false` et HSTS en production
- [x] Ne pas publier les source maps en production
- [x] Créer la mise en page commune en français (`lang="fr"`), la page 404 et une page d'erreur générique
- [x] Tester les en-têtes et l'absence de détail technique dans les pages d'erreur

**Terminé lorsque :** un script en ligne sans nonce est bloqué par le navigateur.

---

## Phase 2 — Intégration continue du frontend

**Objectif :** aucun changement du frontend n'est accepté sans passer les contrôles.

- [x] Rédiger `frontend/docs/steps/step-2.md`
- [x] Ajouter au pipeline une tâche frontend : installation `--frozen-lockfile`, lint, types, tests, build
- [x] Exécuter `pnpm audit` et bloquer sur une vulnérabilité connue
- [x] Épingler les actions par empreinte et restreindre les permissions, comme pour le backend
- [x] Ne partager aucun cache entre exécutions, comme pour le backend (empoisonnement de cache)
- [x] Mesurer la couverture de tests et fixer un seuil

**Terminé lorsque :** une dépendance vulnérable ou un test frontend en échec bloque la pull request.

---

## Phase 3 — Adaptation de l'API au navigateur

**Objectif :** permettre au navigateur d'utiliser l'API sans jamais exposer le jeton de
renouvellement à JavaScript, ni ouvrir de faille CSRF.

- [x] Rédiger `frontend/docs/steps/step-3.md`
- [x] Poser le jeton de renouvellement dans un cookie `__Secure-`, `HttpOnly`, `Secure`,
      `SameSite=Strict`, limité au chemin `/api/v1/auth/refresh`, à la connexion et à chaque rotation
- [x] Lire ce cookie dans `POST /auth/refresh` ; l'effacer à la déconnexion (simple ou globale) et
      à tout renouvellement refusé
- [x] Ne plus renvoyer le jeton de renouvellement dans aucun corps : flux par corps supprimé
- [x] Protéger les routes à cookie contre le CSRF : origine vérifiée contre la liste autorisée et
      en-tête personnalisé obligatoire
- [x] Activer `allow_credentials` dans CORS pour les seules origines explicites du frontend
- [x] Mettre à jour le contrat OpenAPI, le README et les tests de sécurité du backend
- [x] Tester : cookie absent ou falsifié, origine étrangère, en-tête CSRF manquant, réutilisation

**Terminé lorsque :** une page d'une autre origine ne peut ni renouveler ni fermer la session, et
aucune réponse de l'API ne contient plus le jeton de renouvellement.

---

## Phase 4 — Client API et gestion de session

**Objectif :** un unique point d'accès à l'API, typé, qui gère la session sans jamais stocker de
jeton de façon persistante.

- [ ] Rédiger `frontend/docs/steps/step-4.md`
- [ ] Générer les types depuis le contrat OpenAPI de l'API (outil à justifier)
- [ ] Créer le client HTTP unique : en-tête `Authorization`, délai d'expiration, `credentials`
      réservé aux routes d'authentification
- [ ] Valider à l'exécution les réponses de l'API, traitées comme non fiables
- [ ] Traduire les erreurs `application/problem+json` en messages génériques ; gérer `401`, `404`,
      `409` et `429` avec `Retry-After`
- [ ] Conserver le jeton d'accès en mémoire, et le renouveler avant expiration grâce à `expires_in`
- [ ] Restaurer la session au chargement de la page par un renouvellement
- [ ] Coordonner le renouvellement entre onglets (Web Locks) pour ne pas déclencher la détection
      de réutilisation, et diffuser la déconnexion (BroadcastChannel)
- [ ] Tester : aucun jeton dans le stockage du navigateur, renouvellements concurrents, expiration

**Terminé lorsque :** deux onglets ouverts pendant plus de 15 minutes gardent leur session, et aucun
jeton n'apparaît dans `localStorage`, `sessionStorage` ni `document.cookie`.

---

## Phase 5 — Parcours de compte

**Objectif :** des écrans de compte qui ne révèlent pas quelles adresses sont inscrites.

- [ ] Rédiger `frontend/docs/steps/step-5.md`
- [ ] Implémenter l'inscription, avec le même message de confirmation quelle que soit l'adresse
- [ ] Implémenter `/verify-email` et `/reset-password` : lire le jeton dans le fragment, l'effacer
      aussitôt de l'URL, puis l'envoyer dans un corps `POST`
- [ ] Implémenter la connexion avec le message unique `identifiants invalides`
- [ ] Implémenter mot de passe oublié et réinitialisation
- [ ] Implémenter la déconnexion et la déconnexion de tous les appareils
- [ ] Champs de mot de passe : 12 à 128 caractères, collage autorisé, attributs `autocomplete`,
      aucune règle de composition
- [ ] Protéger les pages privées côté client, en rappelant que seule l'API fait foi
- [ ] Tester chaque parcours, dont un jeton expiré ou déjà utilisé

**Terminé lorsque :** les écrans et les messages ne permettent pas de distinguer une adresse connue
d'une adresse inconnue, et aucun jeton à usage unique ne reste dans l'historique du navigateur.

---

## Phase 6 — Tâches

**Objectif :** gérer ses tâches sans qu'un contenu saisi puisse jamais s'exécuter comme du code.

- [ ] Rédiger `frontend/docs/steps/step-6.md`
- [ ] Afficher la liste des tâches avec la pagination par curseur de l'API
- [ ] Créer une tâche avec ses jours, en reprenant les limites de l'API (titre, description, 31 jours)
- [ ] Afficher le détail d'une tâche, modifier et supprimer avec confirmation
- [ ] Rendre titres et descriptions comme du texte, jamais comme du HTML ou du Markdown interprété
- [ ] Gérer les réponses `404` et `409` sans révéler d'information
- [ ] Tester l'affichage d'une charge XSS, les limites de saisie et les erreurs

**Terminé lorsque :** une tâche intitulée `<img src=x onerror=alert(1)>` s'affiche telle quelle,
sans exécution.

---

## Phase 7 — Grille hebdomadaire

**Objectif :** la grille visée par le projet : une ligne par jour, une colonne par heure.

- [ ] Rédiger `frontend/docs/steps/step-7.md`
- [ ] Afficher `GET /planning` sur une semaine, avec navigation bornée à 31 jours
- [ ] N'afficher que le titre dans la grille ; charger la description au clic
- [ ] Placer, déplacer et retirer une tâche d'un jour, avec heure de début et durée
- [ ] Changer le statut d'une tâche pour un jour
- [ ] Filtrer par statut et par priorité
- [ ] Traduire un chevauchement (`409`) en message clair, sans état incohérent à l'écran
- [ ] Rendre la grille utilisable au clavier et par un lecteur d'écran
- [ ] Tester les bornes de période, les conflits et la navigation clavier

**Terminé lorsque :** une semaine complète se planifie au clavier, et un conflit de créneau est
signalé sans corrompre l'affichage.

---

## Phase 8 — Compte et RGPD

**Objectif :** donner à l'utilisateur la maîtrise de son compte et de ses données.

- [ ] Rédiger `frontend/docs/steps/step-8.md`
- [ ] Afficher le profil (`GET /users/me`)
- [ ] Changer le mot de passe avec réauthentification, puis reconnecter l'utilisateur
- [ ] Télécharger l'export des données sans jamais placer de jeton dans une URL
- [ ] Supprimer le compte avec réauthentification et confirmation explicite
- [ ] Effacer tout état local après une suppression ou une révocation
- [ ] Tester chaque parcours et l'état du navigateur après suppression

**Terminé lorsque :** après suppression du compte, plus aucune donnée de l'utilisateur ne subsiste
dans l'onglet ni dans les autres onglets ouverts.

---

## Phase 9 — Accessibilité, tests de bout en bout et audit

**Objectif :** vérifier l'application entière face à des référentiels, sur la vraie pile.

- [ ] Rédiger `frontend/docs/steps/step-9.md`
- [ ] Ajouter Playwright et des tests de bout en bout contre l'API et PostgreSQL réels
- [ ] Vérifier l'isolation entre deux utilisateurs depuis l'interface
- [ ] Contrôler l'accessibilité (WCAG 2.2 AA) avec un outil automatisé et une revue manuelle
- [ ] Rechercher dans le bundle de production toute URL interne, secret ou source map
- [ ] Parcourir les exigences applicables de l'OWASP ASVS côté client
- [ ] Lancer un scan OWASP ZAP sur le frontend local
- [ ] Corriger chaque constat ou documenter son acceptation

**Terminé lorsque :** chaque constat d'audit a une preuve : test, configuration ou décision écrite.

---

## Phase 10 — Conteneurisation et déploiement

**Objectif :** servir le frontend dans la pile de production, de façon reproductible et minimale.

- [ ] Rédiger `frontend/docs/steps/step-10.md`
- [ ] Construire en sortie `standalone`, dans un `Dockerfile` multi-étapes, non root, image épinglée
      par empreinte
- [ ] Ajouter le frontend à `deploy/compose.prod.yaml`, derrière nginx et TLS
- [ ] Configurer l'origine du frontend dans l'API (`frontend_base_url`, CORS) en production
- [ ] Générer un SBOM et analyser l'image dans la CI
- [ ] Compléter la section Installation du README avec les commandes réellement exécutées

**Terminé lorsque :** un parcours complet (inscription, planification, suppression du compte)
fonctionne sur la pile de production locale.

---

## Après la version 1 du frontend

- [ ] Mode hors ligne (à évaluer : un cache local de données personnelles a un coût de sécurité)
- [ ] Interface en plusieurs langues
- [ ] Thème sombre
