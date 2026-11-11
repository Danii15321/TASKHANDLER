"""Tests de la grille : chevauchements, statuts, priorités, période, filtres et pagination."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from http import HTTPStatus
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.modules.tasks.service import MAX_PLANNING_ENTRIES
from tests.conftest import OutboxClient, bearer, create_account, login_tokens

pytestmark = pytest.mark.anyio

TASKS_URL = "/api/v1/tasks"
PLANNING_URL = "/api/v1/planning"
ENTRY_FIELDS = {"task_id", "title", "priority", "day", "start_hour", "duration_hours", "status"}
DESCRIPTION = "Description privée, jamais dans la grille"


# requires : `client` est relié à l'application de test.
# assigns  : crée un compte vérifié et ouvre une session.
# ensures  : renvoie l'en-tête d'authentification de ce compte.
async def headers_for(client: OutboxClient, email: str) -> dict[str, str]:
    await create_account(client, email=email)
    return bearer((await login_tokens(client, email=email))["access_token"])


# requires : `isolated_client` est relié à l'application de test.
# assigns  : crée le compte d'Alice.
# ensures  : fournit l'en-tête d'authentification d'Alice.
@pytest.fixture
async def alice(isolated_client: OutboxClient) -> dict[str, str]:
    return await headers_for(isolated_client, "alice@exemple.org")


# requires : `client` est relié à l'application, `headers` authentifie un compte.
# assigns  : envoie une création de tâche.
# ensures  : renvoie la réponse HTTP brute.
async def post_task(
    client: OutboxClient,
    headers: dict[str, str],
    days: list[tuple[str, int, int]],
    title: str = "Tâche",
    priority: str = "medium",
) -> Any:
    body = {
        "title": title,
        "description": DESCRIPTION,
        "priority": priority,
        "days": [{"day": d, "start_hour": s, "duration_hours": n} for d, s, n in days],
    }
    return await client.post(TASKS_URL, json=body, headers=headers)


# requires : `client` est relié à l'application, `headers` authentifie un compte.
# assigns  : crée une tâche.
# ensures  : renvoie la tâche créée ; échoue si la création est refusée.
async def create_task(
    client: OutboxClient,
    headers: dict[str, str],
    days: list[tuple[str, int, int]],
    title: str = "Tâche",
    priority: str = "medium",
) -> dict[str, Any]:
    response = await post_task(client, headers, days, title, priority)
    assert response.status_code == HTTPStatus.CREATED, response.text
    task: dict[str, Any] = response.json()
    return task


# requires : `client` est relié à l'application.
# assigns  : rien.
# ensures  : renvoie la réponse de la grille pour ces paramètres.
async def planning(client: OutboxClient, headers: dict[str, str], params: Any) -> Any:
    return await client.get(PLANNING_URL, params=params, headers=headers)


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée des tâches, dans une transaction annulée à la fin du test.
# ensures  : critère de la roadmap : deux créneaux qui se chevauchent sont refusés en 409, à la
#            création comme par `PUT` ; deux créneaux qui se touchent (10 h) sont acceptés.
async def test_overlapping_slots_are_refused(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    await create_task(isolated_client, alice, [("2026-11-16", 9, 2)])

    overlap = await post_task(isolated_client, alice, [("2026-11-16", 10, 2)])
    touching = await post_task(isolated_client, alice, [("2026-11-16", 11, 1)])
    other = await create_task(isolated_client, alice, [("2026-11-17", 9, 1)])
    moved = await isolated_client.put(
        f"{TASKS_URL}/{other['id']}/days/2026-11-16",
        json={"start_hour": 8, "duration_hours": 2},
        headers=alice,
    )

    assert overlap.status_code == HTTPStatus.CONFLICT
    assert overlap.json()["detail"] == "Ce créneau chevauche une autre tâche."
    assert touching.status_code == HTTPStatus.CREATED
    assert moved.status_code == HTTPStatus.CONFLICT


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée des tâches pour deux comptes, dans une transaction annulée à la fin du test.
# ensures  : l'interdiction vaut par utilisateur : Alice et Bob peuvent occuper la même heure.
async def test_overlap_is_per_user(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    bob = await headers_for(isolated_client, "bob@exemple.org")
    await create_task(isolated_client, alice, [("2026-11-16", 9, 2)])

    response = await post_task(isolated_client, bob, [("2026-11-16", 9, 2)])

    assert response.status_code == HTTPStatus.CREATED


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : annule puis réactive un jour, dans une transaction annulée à la fin du test.
# ensures  : un jour annulé libère son créneau ; le réactiver sur un créneau repris est refusé en
#            409, et le jour reste annulé.
async def test_cancelled_day_frees_its_slot(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    first = await create_task(isolated_client, alice, [("2026-11-16", 9, 2)])
    url = f"{TASKS_URL}/{first['id']}/days/2026-11-16"

    cancelled = await isolated_client.patch(url, json={"status": "cancelled"}, headers=alice)
    taken = await post_task(isolated_client, alice, [("2026-11-16", 9, 1)])
    reactivated = await isolated_client.patch(url, json={"status": "todo"}, headers=alice)

    assert cancelled.status_code == HTTPStatus.OK
    assert taken.status_code == HTTPStatus.CREATED
    assert reactivated.status_code == HTTPStatus.CONFLICT
    current = (await isolated_client.get(f"{TASKS_URL}/{first['id']}", headers=alice)).json()
    assert current["days"][0]["status"] == "cancelled"


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : change le statut d'un jour, dans une transaction annulée à la fin du test.
# ensures  : les statuts changent librement ; `completed_at` est daté au passage à `done`, gardé
#            si le jour est déjà fait, et effacé en le quittant.
async def test_status_transitions_manage_completed_at(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    task = await create_task(isolated_client, alice, [("2026-11-16", 9, 1)])
    url = f"{TASKS_URL}/{task['id']}/days/2026-11-16"

    states = []
    for status in ("in_progress", "done", "done", "todo", "cancelled", "done"):
        response = await isolated_client.patch(url, json={"status": status}, headers=alice)
        assert response.status_code == HTTPStatus.OK
        states.append((response.json()["status"], response.json()["completed_at"]))

    assert states[0] == ("in_progress", None)
    assert states[1][1] is not None
    assert states[2] == states[1]
    assert states[3] == ("todo", None)
    assert states[4] == ("cancelled", None)
    assert states[5][0] == "done"
    assert states[5][1] is not None


# requires : `body` est un changement de statut invalide.
# assigns  : rien.
# ensures  : un statut inconnu, un `completed_at` fourni par le client ou un champ inconnu sont
#            refusés en 422.
@pytest.mark.parametrize(
    "body",
    [
        {"status": "fini"},
        {},
        {"status": "done", "completed_at": "2026-01-01T00:00:00Z"},
        {"status": "done", "start_hour": 3},
    ],
)
async def test_invalid_status_change_is_refused(
    isolated_client: OutboxClient, alice: dict[str, str], body: dict[str, Any]
) -> None:
    task = await create_task(isolated_client, alice, [("2026-11-16", 9, 1)])

    response = await isolated_client.patch(
        f"{TASKS_URL}/{task['id']}/days/2026-11-16", json=body, headers=alice
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée et modifie des tâches, dans une transaction annulée à la fin du test.
# ensures  : la priorité vaut `medium` par défaut, se modifie, et n'accepte que trois valeurs.
async def test_priority(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    task = await create_task(isolated_client, alice, [("2026-11-16", 9, 1)], priority="high")
    url = f"{TASKS_URL}/{task['id']}"

    lowered = await isolated_client.patch(url, json={"priority": "low"}, headers=alice)
    invalid = await isolated_client.patch(url, json={"priority": "urgent"}, headers=alice)
    refused = await post_task(isolated_client, alice, [("2026-11-17", 9, 1)], priority="urgent")

    assert task["priority"] == "high"
    assert lowered.json()["priority"] == "low"
    assert invalid.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée des tâches, dans une transaction annulée à la fin du test.
# ensures  : la grille renvoie les cases de la période, triées, avec titre et position, et jamais
#            la description ; les jours hors période n'y figurent pas.
async def test_planning_returns_light_entries(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    first = await create_task(
        isolated_client, alice, [("2026-11-17", 14, 1), ("2026-11-16", 9, 2)], title="Cours"
    )
    await create_task(isolated_client, alice, [("2026-12-30", 9, 1)], title="Plus tard")

    response = await planning(isolated_client, alice, {"start": "2026-11-16", "end": "2026-11-22"})

    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert (body["start"], body["end"]) == ("2026-11-16", "2026-11-22")
    assert [(e["day"], e["start_hour"]) for e in body["entries"]] == [
        ("2026-11-16", 9),
        ("2026-11-17", 14),
    ]
    assert all(set(entry) == ENTRY_FIELDS for entry in body["entries"])
    assert body["entries"][0]["task_id"] == first["id"]
    assert DESCRIPTION not in response.text
    assert "description" not in response.text


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée des tâches de priorités et statuts variés, dans une transaction annulée.
# ensures  : les filtres par statut et par priorité se combinent, et acceptent plusieurs valeurs.
async def test_planning_filters(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    high = await create_task(isolated_client, alice, [("2026-11-16", 9, 1)], priority="high")
    await create_task(isolated_client, alice, [("2026-11-16", 10, 1)], priority="low")
    await isolated_client.patch(
        f"{TASKS_URL}/{high['id']}/days/2026-11-16", json={"status": "done"}, headers=alice
    )
    period = [("start", "2026-11-16"), ("end", "2026-11-16")]

    done = await planning(isolated_client, alice, [*period, ("status", "done")])
    low = await planning(isolated_client, alice, [*period, ("priority", "low")])
    both = await planning(
        isolated_client, alice, [*period, ("priority", "low"), ("priority", "high")]
    )
    none = await planning(
        isolated_client, alice, [*period, ("status", "done"), ("priority", "low")]
    )

    assert [e["priority"] for e in done.json()["entries"]] == ["high"]
    assert [e["priority"] for e in low.json()["entries"]] == ["low"]
    assert len(both.json()["entries"]) == 2
    assert none.json()["entries"] == []


# requires : `params` est une demande de grille invalide.
# assigns  : rien.
# ensures  : période inversée, de plus de 31 jours, jour non ISO 8601, filtre inconnu ou paramètre
#            inconnu : la demande est refusée en 422.
@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-11-22", "end": "2026-11-16"},
        {"start": "2026-11-01", "end": "2026-12-02"},
        {"start": "2026-11-16"},
        {"start": "16/11/2026", "end": "2026-11-22"},
        {"start": "1700000000", "end": "2026-11-22"},
        {"start": "2026-11-16T00:00:00", "end": "2026-11-22"},
        {"start": "2026-11-16", "end": "2026-11-22", "status": "fini"},
        {"start": "2026-11-16", "end": "2026-11-22", "priority": "urgent"},
        {"start": "2026-11-16", "end": "2026-11-22", "user_id": str(uuid.uuid4())},
        {"start": "1999-12-31", "end": "2000-01-01"},
    ],
)
async def test_invalid_planning_request_is_refused(
    isolated_client: OutboxClient, alice: dict[str, str], params: dict[str, str]
) -> None:
    response = await planning(isolated_client, alice, params)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : rien.
# ensures  : une période de 31 jours exactement est acceptée.
async def test_planning_accepts_thirty_one_days(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    response = await planning(isolated_client, alice, {"start": "2026-12-01", "end": "2026-12-31"})

    assert response.status_code == HTTPStatus.OK


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée des tâches pour Alice, dans une transaction annulée à la fin du test.
# ensures  : la grille et la liste de Bob ne contiennent rien d'Alice.
async def test_planning_and_list_are_isolated(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    await create_task(isolated_client, alice, [("2026-11-16", 9, 1)], title="Secret d'Alice")
    bob = await headers_for(isolated_client, "bob@exemple.org")

    grid = await planning(isolated_client, bob, {"start": "2026-11-16", "end": "2026-11-16"})
    listing = await isolated_client.get(TASKS_URL, headers=bob)

    assert grid.json()["entries"] == []
    assert listing.json() == {"items": [], "next_cursor": None}
    assert "Secret" not in grid.text + listing.text


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : insère en SQL de nombreux jours annulés, dans une transaction annulée à la fin.
# ensures  : une grille démesurée est refusée en 422, plutôt que de renvoyer des milliers de cases.
async def test_oversized_planning_is_refused(
    isolated_client: OutboxClient, alice: dict[str, str], db_session: AsyncSession
) -> None:
    await create_task(isolated_client, alice, [("2026-12-01", 0, 1)])
    # Les jours annulés peuvent se superposer : c'est le seul moyen de dépasser la borne.
    await db_session.execute(
        text(
            "WITH owner AS (SELECT user_id FROM tasks LIMIT 1), "
            "new_tasks AS ("
            "  INSERT INTO tasks (user_id, title, description) "
            "  SELECT owner.user_id, 'T' || n, 'D' FROM owner, generate_series(1, 40) AS n "
            "  RETURNING id, user_id) "
            "INSERT INTO task_days (task_id, user_id, day, start_hour, duration_hours, status) "
            "SELECT id, user_id, DATE '2026-12-01' + d, 8, 1, 'cancelled' "
            "FROM new_tasks, generate_series(0, 30) AS d"
        )
    )

    response = await planning(isolated_client, alice, {"start": "2026-12-01", "end": "2026-12-31"})

    assert MAX_PLANNING_ENTRIES < 40 * 31
    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée cinq tâches, dans une transaction annulée à la fin du test.
# ensures  : la liste se parcourt par pages, triée par création décroissante puis identifiant,
#            sans tâche sautée ni répétée ; la dernière page n'a pas de curseur ; aucune
#            description.
async def test_task_list_is_paginated_by_cursor(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    created = [
        await create_task(isolated_client, alice, [(f"2026-11-{16 + n}", 9, 1)], title=f"T{n}")
        for n in range(5)
    ]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = {"limit": "2", **({"cursor": cursor} if cursor else {})}
        page = (await isolated_client.get(TASKS_URL, params=params, headers=alice)).json()
        seen += [item["title"] for item in page["items"]]
        pages += 1
        assert all("description" not in item for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    # Dans un test isolé, les cinq tâches partagent une transaction, donc la même `created_at` :
    # c'est le cas le plus exigeant, où seul l'identifiant départage les tâches.
    expected = sorted(created, key=lambda task: (task["created_at"], task["id"]), reverse=True)
    assert pages == 3
    assert seen == [task["title"] for task in expected]


# requires : `params` est une demande de liste invalide.
# assigns  : rien.
# ensures  : page trop grande ou nulle, curseur falsifié ou paramètre inconnu : 422.
@pytest.mark.parametrize(
    "params",
    [
        {"limit": "101"},
        {"limit": "0"},
        {"limit": "abc"},
        {"cursor": "pas-un-curseur"},
        {"cursor": "MjAyNi0xMS0xNnxwYXMtdW4tdXVpZA"},
        {"cursor": "x" * 300},
        {"sort": "title"},
    ],
)
async def test_invalid_list_request_is_refused(
    isolated_client: OutboxClient, alice: dict[str, str], params: dict[str, str]
) -> None:
    response = await isolated_client.get(TASKS_URL, params=params, headers=alice)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte et deux tâches réellement validés, supprimés à la fin du test.
# ensures  : fournit le client, l'en-tête et les identifiants des deux tâches.
@pytest.fixture
async def committed_tasks(
    database_client: OutboxClient, app_engine: AsyncEngine
) -> AsyncIterator[tuple[OutboxClient, dict[str, str], list[str]]]:
    email = f"grille-{uuid.uuid4().hex[:12]}@exemple.org"
    account_id = await create_account(database_client, email=email)
    try:
        headers = bearer((await login_tokens(database_client, email=email))["access_token"])
        tasks = [
            await create_task(database_client, headers, [("2026-11-20", 6, 1)]),
            await create_task(database_client, headers, [("2026-11-21", 6, 1)]),
        ]
        yield database_client, headers, [str(task["id"]) for task in tasks]
    finally:
        # Suppression ciblée du seul compte créé ; ses tâches et jours suivent par cascade.
        async with app_engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": account_id})


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : place simultanément deux tâches sur le même créneau, sur deux connexions réelles.
# ensures  : critère de la roadmap : même entre requêtes concurrentes, un seul placement réussit
#            et l'autre reçoit 409. Aucun contrôle en Python (lire puis écrire) n'en serait capable.
async def test_concurrent_overlaps_are_refused(
    committed_tasks: tuple[OutboxClient, dict[str, str], list[str]],
) -> None:
    client, headers, task_ids = committed_tasks
    slot = {"start_hour": 15, "duration_hours": 2}

    responses = await asyncio.gather(
        *(
            client.put(f"{TASKS_URL}/{task_id}/days/2026-11-25", json=slot, headers=headers)
            for task_id in task_ids
        )
    )

    assert sorted(r.status_code for r in responses) == [HTTPStatus.OK, HTTPStatus.CONFLICT]
