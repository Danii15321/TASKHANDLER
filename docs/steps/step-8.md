# Phase 8 — Tâches

## Objectif

Un CRUD complet où un utilisateur ne peut jamais atteindre les données d'un autre.

**Résultat attendu :** chaque route a un test prouvant qu'un second utilisateur reçoit `404`.

Le code complet et commenté se trouve dans `backend/app/modules/tasks/`. Ce guide en explique les
décisions.

## Étape 1 — Deux tables pour une relation N-N

| Table | Colonnes | Rôle |
|---|---|---|
| `tasks` | `id`, `user_id`, `title`, `description`, `created_at`, `updated_at` | La tâche |
| `task_days` | `task_id`, `day`, `user_id`, `start_hour`, `duration_hours` | Son créneau dans un jour |

Une tâche a plusieurs jours, un jour plusieurs tâches. Aucune table `days` : la date (`DATE`) suffit
à identifier un jour. La clé primaire (`task_id`, `day`) garantit un seul créneau par jour et par
tâche.

## Étape 2 — La clé étrangère composite

```sql
UNIQUE (id, user_id)                                   -- dans tasks
FOREIGN KEY (task_id, user_id) REFERENCES tasks (id, user_id) ON DELETE CASCADE
```

`task_days.user_id` copie le propriétaire, et la base **garantit** qu'il est le bon : rattacher le
jour d'Alice au compte de Bob est refusé par PostgreSQL, même si l'application se trompait. Chaque
requête sur les jours peut ainsi filtrer par propriétaire, sans jointure.

## Étape 3 — Les contraintes, en toutes lettres

| Règle | Contrainte |
|---|---|
| Titre de 1 à 200 caractères, sans contrôle | `char_length`, `!~ '[[:cntrl:]]'` |
| Description de 1 à 5 000 caractères | `char_length` ; retours à la ligne permis, autres contrôles refusés |
| Champ non vide | `~ '\S'` : au moins un caractère qui n'est pas un blanc |
| Créneau dans la journée | `start_hour BETWEEN 0 AND 23`, `duration_hours >= 1`, `start_hour + duration_hours <= 24` |
| Jours plausibles | `day BETWEEN '2000-01-01' AND '2100-12-31'` |

> **Défaut corrigé.** La première version vérifiait `char_length(btrim(description)) >= 1`. Or
> `btrim` ne retire que les espaces : `" \n "` passait. L'API refusait ce texte, mais la base, la
> dernière ligne de défense, l'acceptait. Un test qui insère directement en SQL l'a montré.

Les contraintes ne sont jamais construites par f-string, même à partir de constantes. Un test
vérifie que la base accepte exactement les bornes des schémas.

## Étape 4 — Les schémas

- `TaskCreate` : titre, description et de **1 à 31 jours distincts** ;
- `TaskUpdate` : titre et/ou description, au moins un champ, jamais `null` ;
- `TaskDaySlot` : `start_hour` et `duration_hours` en `StrictInt` (`"9"` et `9.0` refusés) ;
- `TaskRead` et `TaskDayRead` : liste blanche, **sans `user_id`**.

Tous ont `extra="forbid"` : un `user_id` injecté donne `422`. Les textes sont nettoyés (espaces
autour retirés), et un champ vide après nettoyage est refusé.

## Étape 5 — Le dépôt : le propriétaire dans chaque `WHERE`

```python
update(Task).where(Task.id == task_id, Task.user_id == user_id).returning(Task)
```

Jamais de lecture suivie d'un contrôle en Python : la tâche d'un autre est **introuvable**. Pour un
jour, le propriétaire est vérifié par la tâche parente, **dans la même instruction** :

```sql
INSERT INTO task_days (...) SELECT :task_id, :day, :user_id, ...
WHERE EXISTS (SELECT 1 FROM tasks WHERE id = :task_id AND user_id = :user_id)
ON CONFLICT (task_id, day) DO UPDATE SET ... WHERE task_days.user_id = :user_id
```

## Étape 6 — Les routes

| Route | Effet |
|---|---|
| `POST /tasks` | Crée la tâche et ses jours (`201`) |
| `GET /tasks/{id}` | Lit la tâche et ses jours |
| `PATCH /tasks/{id}` | Modifie le titre et/ou la description |
| `DELETE /tasks/{id}` | Supprime la tâche et ses jours (`204`) |
| `PUT /tasks/{id}/days/{day}` | Crée ou remplace le créneau d'un jour |
| `DELETE /tasks/{id}/days/{day}` | Retire un jour ; `409` s'il est le dernier |

Le routeur déclare `AUTHENTICATED_DEPENDENCIES` : authentification et limite par utilisateur, pour
toutes ses routes. La tâche d'un autre répond `404`, **jamais `403`**, qui avouerait qu'elle existe.

`GET /tasks`, la liste paginée par curseur, arrive à la phase 9.

## Étape 7 — Le dernier jour, sous verrou

```python
lock_task(...)          # SELECT … FOR UPDATE sur la tâche
remaining = count_task_days(...)
delete_task_day(...)
if remaining <= 1: rollback, puis 409
```

Sans le verrou, deux retraits simultanés des deux derniers jours compteraient chacun « deux jours »,
et laisseraient tous les deux la tâche vide. Un test le prouve, sur deux connexions réelles.

## Étape 8 — La matrice de sécurité

Pour **chaque** route protégée (`test_tasks_security.py`) :

- sans jeton, jeton expiré, falsifié ou d'un autre algorithme : `401`, tâche inchangée ;
- Bob sur la tâche d'Alice, en lecture, modification, suppression, ajout ou retrait de jour : `404`,
  et la tâche d'Alice est **strictement identique** après coup ;
- `user_id`, `id`, `created_at` ou un champ inconnu injecté : `422` ;
- aucune réponse ne contient `user_id` ni de donnée de compte.

## À retenir

- L'isolation se garantit dans la requête SQL, pas après elle.
- Une contrainte composite rend une erreur d'isolation impossible à enregistrer.
- Vérifier une règle « au moins un » exige un verrou, sinon deux requêtes la contournent ensemble.

**Normes concernées :** OWASP API Security Top 10 2023, API1 (accès aux objets), API3 (propriétés),
API5 (fonctions) ; OWASP ASVS, chapitre V4 (contrôle d'accès).
