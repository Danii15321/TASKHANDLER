# Phase 10 — Durcissement, RGPD et audit

## Objectif

Donner à l'utilisateur la maîtrise de son compte, durcir la couche HTTP, puis vérifier l'API
entière face à l'OWASP API Security Top 10 et à l'ASVS.

**Résultat attendu :** chaque point du Top 10 a une preuve : un test, une configuration ou une
décision écrite.

Code : `app/modules/users/`, `app/core/security_headers.py`, `app/core/openapi.py`,
`app/maintenance.py` et la migration `0007`.

## Étape 1 — Les routes du compte

| Route | Preuve demandée | Effet |
|---|---|---|
| `GET /users/me` | Jeton | Vue publique : `id`, `email`, `email_verified_at`, `created_at` |
| `POST /users/me/password` | Jeton + mot de passe actuel | Nouveau hash, **toutes** les sessions coupées |
| `DELETE /users/me` | Jeton + mot de passe | Compte, tâches, jours et jetons supprimés en cascade |
| `GET /users/me/export` | Jeton | Toutes les données du compte, en pièce jointe JSON |

- **Réauthentification** : un jeton volé ne suffit pas pour une action irréversible. Un mot de
  passe faux donne `403` (le jeton est valide, c'est la preuve en plus qui manque) ; le compteur
  est limité à 5 essais par minute, sinon un jeton volé servirait à deviner le mot de passe.
- **Fermé si Redis tombe** : ces trois routes répondent `503` sans Redis. Les autres routes
  authentifiées restent ouvertes, pour qu'un utilisateur puisse toujours couper ses sessions.
- **Changement de mot de passe** : même politique qu'à l'inscription, `token_version` incrémenté
  (les jetons d'accès tombent aussitôt) et sessions révoquées avec le motif `password_change`.
- **Export** (droit d'accès et de portabilité, RGPD art. 15 et 20) : description des tâches
  comprise, sessions sans leur empreinte. `Content-Disposition: attachment`,
  `Cache-Control: no-store`, 5 exports par heure.
- **Journal d'audit** : `password_changed`, `account_deleted`, `account_exported`,
  `reauthentication_failed`, avec l'identifiant du compte, jamais son adresse.

## Étape 2 — Conserver le moins longtemps possible

| Donnée | Supprimée après | Point de départ |
|---|---|---|
| Session (jeton de renouvellement) | 30 jours | Révocation, sinon expiration |
| Lien à usage unique | 7 jours | Utilisation, sinon expiration |
| Compte jamais vérifié | 7 jours | Création |

Les durées sont réglables (`TASKHANDLER_RETENTION_*`). La purge est une commande, pas une route :
aucune requête HTTP ne peut la déclencher. Elle se lance depuis `backend/`, par une tâche planifiée
en production :

```bash
uv run --locked python -m app.maintenance purge
```

Le journal `retention_purge` ne contient que des nombres. Un compte vérifié n'est jamais purgé.

## Étape 3 — Sécurité au niveau des lignes (RLS)

**Décision : activée sur `tasks` et `task_days`.** Le filtre `WHERE user_id` reste dans chaque
requête (règle du projet) ; la RLS est un **second verrou** posé par PostgreSQL, qui rattrape un
oubli dans le code.

```sql
CREATE POLICY tasks_owner ON tasks FOR ALL TO taskhandler_app
USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid);
```

- `get_current_user` déclare l'utilisateur avec `set_config(…, true)` : la valeur ne vit que le
  temps de la **transaction**, elle ne fuit donc jamais vers la requête suivante du pool.
  Un écouteur `after_begin` la redéclare après chaque `commit`.
- Sans utilisateur déclaré, la condition vaut `NULL` : aucune ligne visible.
- Le rôle applicatif ne possède pas les tables : il ne peut ni désactiver la RLS ni supprimer une
  politique (`42501`, testé). Le rôle de migration, propriétaire, n'y est pas soumis.
- Non retenue sur `users` et les jetons : la connexion et la réinitialisation lisent un compte
  **avant** de connaître l'utilisateur. La politique serait contournée par construction.

## Étape 4 — Durcir la couche HTTP

| Mesure | Valeur |
|---|---|
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'` |
| `X-Content-Type-Options` / `X-Frame-Options` | `nosniff` / `DENY` |
| `Referrer-Policy` / `Cache-Control` | `no-referrer` / `no-store` |
| `Cross-Origin-Resource-Policy` | `same-origin` (ajouté après le scan ZAP, étape 7) |
| HSTS | `max-age=63072000; includeSubDomains`, production seulement |
| `TrustedHostMiddleware` | `TASKHANDLER_ALLOWED_HOSTS`, `*` refusé au démarrage |
| CORS | Fermé par défaut ; origines exactes, HTTPS hors développement, sans cookies |

Le middleware des en-têtes est le **plus extérieur** : il couvre aussi le `500` générique.

> **Piège rencontré.** Placé sous le middleware de contexte, il laissait ce `500` sans en-têtes.
> Le test paramétré sur 200, 401, 404, 405, 422 et 500 l'a révélé avant la correction.

En développement, `/docs` et `/redoc` gardent leur propre politique (ils chargent des scripts) ;
`/openapi.json` reste strict. Le contrat OpenAPI décrit enfin les erreurs `422` au format
`application/problem+json` réellement renvoyé.

## Étape 5 — OWASP API Security Top 10 (2023)

| Risque | Preuve |
|---|---|
| API1 — Accès aux objets | `WHERE user_id` partout + RLS : `test_tasks_security.py`, `test_rls.py` |
| API2 — Authentification | argon2id, JWT strict, rotation : `test_passwords.py`, `test_tokens.py`, `test_sessions.py`, `test_login.py` |
| API3 — Propriétés d'objets | `extra="forbid"`, `response_model` : `user_id` injecté → `422`, aucun hash exporté (`test_account.py`) |
| API4 — Consommation | Corps ≤ 1 Mio, pages ≤ 100, grille ≤ 31 jours, hachages bornés, export 5/h |
| API5 — Fonctions | `test_openapi_contract.py` : toute route non publique exige un jeton **et** la limite |
| API6 — Flux métier | `202` uniformes et limites par IP et adresse : `test_abuse_protection.py` |
| API7 — SSRF | Aucune requête vers une URL fournie : SMTP et `frontend_base_url` viennent de la configuration |
| API8 — Configuration | `test_config.py`, `test_http_hardening.py` : debug interdit, docs coupées, CORS, hôtes |
| API9 — Inventaire | `/api/v1` imposé, contrat = routes déclarées (`test_openapi_contract.py`) |
| API10 — Services tiers | Réponses Redis bornées (`test_rate_limit.py`) ; SMTP chiffré en production, en-têtes injectés refusés (`test_email_delivery.py`) |

## Étape 6 — OWASP ASVS 5.0 (niveau 2 visé)

| Chapitre | État |
|---|---|
| V1 Encodage, V2 Validation | Couvert : Pydantic strict, caractères de contrôle refusés, SQL paramétré |
| V4 API | Couvert : `problem+json`, méthodes sans effet de bord en `GET`, CORS explicite |
| V6 Authentification | Couvert : 12 à 128 caractères, liste de mots de passe courants, réponse unique |
| V7 Sessions, V9 Jetons | Couvert : 15 min, `sid`, `ver`, rotation, révocation à chaque changement de secret |
| V8 Autorisation | Couvert : refus par défaut, `404` hors propriétaire, RLS |
| V11 Cryptographie | Couvert : `secrets`, SHA-256 des jetons opaques, `compare_digest` |
| V13 Configuration, V16 Journaux | Couvert : secrets validés au démarrage, journaux filtrés, audit |
| V14 Données | Couvert : export, suppression, conservation, `no-store` |
| V12 Communications | **Phase 11** : TLS du proxy et vers PostgreSQL |
| V3, V5, V10, V17 | Non applicables : ni navigateur servi, ni fichier, ni OAuth, ni WebRTC |

## Étape 7 — Scan dynamique OWASP ZAP

ZAP 2.17.0 (`ghcr.io/zaproxy/zaproxy`, épinglé par empreinte `sha256:9624669d…`), mode API :
il lit `/openapi.json`, puis attaque chaque route. L'API tourne en développement sur la pile
jetable, avec des limites relevées **pour ce scan seulement**, sinon tout finirait en `429`.

```bash
docker run --rm --network host -e ZAP_AUTH_HEADER_VALUE -v "$PWD/zap:/zap/wrk:rw" \
  ghcr.io/zaproxy/zaproxy@sha256:<empreinte> zap-api-scan.py -t /zap/wrk/openapi.json \
  -f openapi -O http://127.0.0.1:58100 -J report.json -r report.html
```

- **Scan authentifié** : `ZAP_AUTH_HEADER_VALUE="Bearer <jeton>"` (et non l'option `-z`, qui coupe
  la valeur à l'espace). Les routes de déconnexion sont retirées du contrat donné à ZAP : appelées,
  elles révoquent son jeton et le reste du scan tourne sans authentification.
- **Vérifier le journal de l'API, pas seulement le rapport** : le premier essai affichait
  « 0 échec » alors que toutes les routes protégées avaient répondu `401`.

| Constat | Décision |
|---|---|
| `Cross-Origin-Resource-Policy` absent (90004, faible) | **Corrigé** : `same-origin` sur toute réponse, test d'abord |
| Redirections `307` sur `/` final | Accepté : même hôte, `Host` forgé → `400`, `X-Forwarded-Host` ignoré |
| JSON mal formé sans jeton → `422` avant `401` | Accepté : FastAPI décode le corps avant les dépendances ; rien n'est lu ni écrit, l'entrée n'est pas renvoyée |

Résultat final : 0 échec, 0 avertissement, 120 règles passées, 25 réponses `200` sur des routes
protégées, aucun `500` ni erreur serveur dans le journal sur plus de 3 400 requêtes.

## Bonnes pratiques et normes de la phase

- Réauthentifier avant toute action irréversible ; révoquer toutes les sessions après.
- Conserver le minimum, le minimum de temps (RGPD art. 5.1.e) ; purger hors HTTP.
- Défense en profondeur : RLS **en plus** du filtre applicatif, jamais à sa place.
- Tester les en-têtes sur les réponses d'erreur, pas seulement sur les succès.
- Un inventaire de routes testé empêche une route oubliée sans protection (OWASP API5, API9).

## À retenir

La base protège désormais les tâches même contre une erreur du code. Rappel : la base locale doit
être migrée (`uv run --locked alembic upgrade head`) pour appliquer `0007`.
