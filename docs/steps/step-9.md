# Phase 9 — Grille hebdomadaire

## Objectif

Fournir à la grille des données légères et fiables, sans aucun chevauchement.

**Résultat attendu :** deux tâches d'un même utilisateur ne peuvent jamais occuper la même case,
même lorsque deux requêtes arrivent en même temps.

Le code complet et commenté se trouve dans `backend/app/modules/tasks/`. Ce guide en explique les
décisions.

## Étape 1 — Interdire le chevauchement en base

Vérifier en Python (« lire les créneaux, puis écrire si c'est libre ») ne suffit pas : deux
requêtes simultanées lisent toutes les deux « libre », et écrivent toutes les deux. La base doit
l'interdire elle-même :

```sql
ALTER TABLE task_days ADD CONSTRAINT ex_task_days_no_overlap
EXCLUDE USING gist (
  user_id WITH =, day WITH =, int4range(start_hour, start_hour + duration_hours) WITH &&
) WHERE (status <> 'cancelled')
```

- même utilisateur, même jour, intervalles qui se chevauchent : refusé ;
- `[début, fin)` : 9 h à 10 h et 10 h à 11 h se **touchent** sans se chevaucher ;
- `WHERE status <> 'cancelled'` : un jour annulé libère son créneau.

L'extension `btree_gist` permet l'égalité sur `uuid` et `date` dans un index GiST. Elle est « de
confiance » : le rôle de migration l'installe sans être superutilisateur.

> **Piège rencontré.** `op.create_exclude_constraint` d'Alembic ne sait pas traiter l'expression
> `int4range(…)`. La contrainte est donc écrite en SQL littéral, sans paramètre ni f-string.

## Étape 2 — Traduire le conflit en 409

PostgreSQL lève une violation d'exclusion (`23P01`). Le service reconnaît le **nom** de la
contrainte, annule la transaction et lève `SlotConflictError` ; la route répond `409`. Toute autre
erreur d'intégrité remonte telle quelle (`500` générique). Trois chemins peuvent la déclencher :
création, `PUT` d'un jour, et réactivation d'un jour annulé dont le créneau a été repris.

## Étape 3 — Statut, `completed_at` et priorité

| Colonne | Valeurs | Garantie en base |
|---|---|---|
| `tasks.priority` | `low`, `medium` (défaut), `high` | `CHECK` |
| `task_days.status` | `todo` (défaut), `in_progress`, `done`, `cancelled` | `CHECK` |
| `task_days.completed_at` | Daté par le serveur | `(status = 'done') = (completed_at IS NOT NULL)` |

Les transitions sont libres. `PATCH /tasks/{id}/days/{day}` ne reçoit que `status` :
`completed_at` est daté au passage à `done` (et conservé si le jour était déjà fait), effacé en le
quittant. Un client qui envoie `completed_at` reçoit `422`.

## Étape 4 — `GET /planning`

```text
GET /api/v1/planning?start=2026-11-16&end=2026-11-22&status=todo&priority=high
```

| Règle | Raison |
|---|---|
| 31 jours au plus, fin après début | Réponse bornée (OWASP API4) |
| Jours au format `AAAA-MM-JJ` uniquement | Pydantic accepterait sinon un horodatage Unix |
| Paramètre inconnu refusé (`extra="forbid"`) | Aucun filtre caché ou oublié |
| Filtres limités aux valeurs connues | Pas de tri ni de filtre libre |
| Au plus 1 000 cases, sinon `422` | Les jours annulés peuvent s'empiler sans limite |

Chaque case contient `task_id`, `title`, `priority`, `day`, `start_hour`, `duration_hours` et
`status`, **jamais la description** : la requête ne lit même pas cette colonne.

## Étape 5 — `GET /tasks` par curseur

`OFFSET` relit et jette toutes les lignes précédentes, et saute ou répète des tâches si la liste
change entre deux pages. La pagination par clé reprend juste après la dernière tâche vue :

```python
WHERE user_id = :u AND (created_at, id) < (:c, :i) ORDER BY created_at DESC, id DESC LIMIT n + 1
```

- `id` départage deux tâches créées au même instant : le tri est total ;
- une ligne de plus que la page est lue, pour savoir s'il en reste ;
- le curseur est opaque (base64), mais n'a pas besoin d'être signé : le modifier ne fait que
  déplacer le client dans **ses propres** tâches. Mal formé, il donne `422` ;
- `limit` de 1 à 100 ; la liste ne renvoie ni description ni jours.

## Étape 6 — Les index, tirés des requêtes réelles

| Requête | Index |
|---|---|
| Liste paginée | `ix_tasks_user_created_id (user_id, created_at DESC, id DESC)` |
| Grille | `ix_task_days_user_day_start (user_id, day, start_hour)` |

Le premier remplace `ix_tasks_user_id` : il commence par `user_id` et sert donc aussi la clé
étrangère. Un test vérifie avec `EXPLAIN` que PostgreSQL choisit bien ces index (avec
`enable_seqscan = off`, car sur une table presque vide il préférerait la parcourir).

## Étape 7 — Tester

- chevauchement refusé à la création et par `PUT`, accepté entre deux utilisateurs ou bord à bord ;
- deux `PUT` simultanés sur le même créneau, sur deux connexions réelles : un `200`, un `409` ;
- jour annulé qui libère sa case, réactivation refusée si elle est reprise ;
- transitions de statut et `completed_at` ; contraintes vérifiées directement en SQL ;
- grille : tri, filtres combinés, absence de description, bornes, formats refusés, isolation ;
- liste : pages complètes sans doublon même à `created_at` égale, curseur falsifié refusé ;
- nouvelle route `PATCH …/days/{day}` ajoutée à la matrice de sécurité (`401`, `404`, `422`).

## À retenir

- Une règle d'unicité ou d'exclusion se garantit en base, jamais par « lire puis écrire ».
- Ce que la grille n'affiche pas, la requête ne doit même pas le lire.
- Un index se justifie par une requête réelle, et se vérifie avec `EXPLAIN`.

**Normes concernées :** OWASP API Security Top 10 2023, API1, API3 et API4 ; documentation
PostgreSQL, « Exclusion Constraints » et extension `btree_gist`.
