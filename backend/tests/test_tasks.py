"""Tests fonctionnels des tâches : création, lecture, modification, suppression et jours."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from http import HTTPStatus

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.conftest import OutboxClient, bearer, create_account, login_tokens

pytestmark = pytest.mark.anyio

TASKS_URL = "/api/v1/tasks"
TASK_FIELDS = {"id", "title", "description", "priority", "days", "created_at", "updated_at"}
DAY_FIELDS = {"day", "start_hour", "duration_hours", "status", "completed_at"}
NEW_DAY = {"status": "todo", "completed_at": None}


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


# requires : rien.
# assigns  : rien.
# ensures  : renvoie un corps de création valide, aux jours modifiables par le test.
def task_payload(
    days: list[dict[str, object]] | None = None, **fields: object
) -> dict[str, object]:
    return {
        "title": "Réviser le cours",
        "description": "Chapitres 3 et 4,\navec les exercices.",
        "days": days
        if days is not None
        else [
            {"day": "2026-11-17", "start_hour": 9, "duration_hours": 2},
            {"day": "2026-11-16", "start_hour": 14, "duration_hours": 1},
        ],
        **fields,
    }


# requires : `client` est relié à l'application, `headers` authentifie un compte.
# assigns  : crée une tâche pour ce compte.
# ensures  : renvoie la tâche créée ; échoue si la création est refusée.
async def create_task(
    client: OutboxClient, headers: dict[str, str], payload: dict[str, object] | None = None
) -> dict[str, object]:
    response = await client.post(TASKS_URL, json=payload or task_payload(), headers=headers)
    assert response.status_code == HTTPStatus.CREATED, response.text
    task: dict[str, object] = response.json()
    return task


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée une tâche, dans une transaction annulée à la fin du test.
# ensures  : la tâche est créée avec ses jours triés, le titre nettoyé, et seuls les champs publics.
async def test_create_task(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    task = await create_task(isolated_client, alice, task_payload(title="  Réviser le cours  "))

    assert set(task) == TASK_FIELDS
    assert task["title"] == "Réviser le cours"
    assert task["description"] == "Chapitres 3 et 4,\navec les exercices."
    assert task["priority"] == "medium"
    assert task["days"] == [
        {"day": "2026-11-16", "start_hour": 14, "duration_hours": 1, **NEW_DAY},
        {"day": "2026-11-17", "start_hour": 9, "duration_hours": 2, **NEW_DAY},
    ]


# requires : `payload` est un corps de création invalide.
# assigns  : rien (la demande est refusée avant toute écriture).
# ensures  : la création est refusée en 422.
@pytest.mark.parametrize(
    "payload",
    [
        task_payload(days=[]),
        task_payload(
            days=[
                {"day": f"2026-12-{n:02d}", "start_hour": 8, "duration_hours": 1}
                for n in range(1, 32)
            ]
            + [{"day": "2027-01-01", "start_hour": 8, "duration_hours": 1}]
        ),
        task_payload(
            days=[
                {"day": "2026-11-16", "start_hour": 8, "duration_hours": 1},
                {"day": "2026-11-16", "start_hour": 10, "duration_hours": 1},
            ]
        ),
        task_payload(days=[{"day": "2026-11-16", "start_hour": 20, "duration_hours": 5}]),
        task_payload(days=[{"day": "2026-11-16", "start_hour": 24, "duration_hours": 1}]),
        task_payload(days=[{"day": "2026-11-16", "start_hour": 8, "duration_hours": 0}]),
        task_payload(days=[{"day": "2026-11-16", "start_hour": "8", "duration_hours": 1}]),
        task_payload(days=[{"day": "2026-11-16", "start_hour": 8.0, "duration_hours": 1}]),
        task_payload(days=[{"day": "1999-12-31", "start_hour": 8, "duration_hours": 1}]),
        task_payload(days=[{"day": "16/11/2026", "start_hour": 8, "duration_hours": 1}]),
        task_payload(title=""),
        task_payload(title="   "),
        task_payload(title="ligne\nsuivante"),
        task_payload(title="nul\x00"),
        task_payload(title="x" * 201),
        task_payload(description=" "),
        task_payload(description="échappement\x1b[31m"),
        task_payload(description="x" * 5001),
    ],
)
async def test_invalid_task_is_refused(
    isolated_client: OutboxClient, alice: dict[str, str], payload: dict[str, object]
) -> None:
    response = await isolated_client.post(TASKS_URL, json=payload, headers=alice)

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée une tâche de 31 jours, dans une transaction annulée à la fin du test.
# ensures  : la borne de 31 jours est incluse.
async def test_task_accepts_thirty_one_days(
    isolated_client: OutboxClient, alice: dict[str, str]
) -> None:
    days = [{"day": f"2026-12-{n:02d}", "start_hour": 8, "duration_hours": 1} for n in range(1, 32)]

    task = await create_task(isolated_client, alice, task_payload(days=days))

    assert isinstance(task["days"], list)
    assert len(task["days"]) == 31


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée puis lit une tâche, dans une transaction annulée à la fin du test.
# ensures  : la lecture renvoie la même tâche ; une tâche inexistante répond 404.
async def test_read_task(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    task = await create_task(isolated_client, alice)

    response = await isolated_client.get(f"{TASKS_URL}/{task['id']}", headers=alice)
    missing = await isolated_client.get(f"{TASKS_URL}/{uuid.uuid4()}", headers=alice)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == task
    assert missing.status_code == HTTPStatus.NOT_FOUND


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : modifie une tâche, dans une transaction annulée à la fin du test.
# ensures  : seuls les champs envoyés changent, et une demande vide ou qui vide un champ
#            obligatoire est refusée.
async def test_partial_update(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    task = await create_task(isolated_client, alice)
    url = f"{TASKS_URL}/{task['id']}"

    titled = await isolated_client.patch(url, json={"title": "Nouveau titre"}, headers=alice)
    described = await isolated_client.patch(url, json={"description": "Autre"}, headers=alice)

    assert titled.status_code == HTTPStatus.OK
    assert titled.json()["description"] == task["description"]
    assert described.json()["title"] == "Nouveau titre"
    assert described.json()["description"] == "Autre"
    assert described.json()["days"] == task["days"]
    invalid_bodies: list[dict[str, object]] = [
        {},
        {"title": None},
        {"description": ""},
        {"days": []},
    ]
    for invalid in invalid_bodies:
        response = await isolated_client.patch(url, json=invalid, headers=alice)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, invalid


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée puis supprime une tâche, dans une transaction annulée à la fin du test.
# ensures  : la tâche et ses jours disparaissent ; une seconde suppression répond 404.
async def test_delete_task(
    isolated_client: OutboxClient, alice: dict[str, str], db_session: AsyncSession
) -> None:
    task = await create_task(isolated_client, alice)
    url = f"{TASKS_URL}/{task['id']}"

    assert (await isolated_client.delete(url, headers=alice)).status_code == HTTPStatus.NO_CONTENT
    assert (await isolated_client.get(url, headers=alice)).status_code == HTTPStatus.NOT_FOUND
    assert (await isolated_client.delete(url, headers=alice)).status_code == HTTPStatus.NOT_FOUND
    days = (await db_session.execute(text("SELECT count(*) FROM task_days"))).scalar_one()
    assert days == 0


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : ajoute puis remplace un jour, dans une transaction annulée à la fin du test.
# ensures  : `PUT` crée un jour absent, remplace le créneau d'un jour présent, et refuse un
#            créneau ou un jour invalide.
async def test_put_task_day(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    task = await create_task(isolated_client, alice)
    url = f"{TASKS_URL}/{task['id']}/days"

    added = await isolated_client.put(
        f"{url}/2026-11-20", json={"start_hour": 18, "duration_hours": 2}, headers=alice
    )
    replaced = await isolated_client.put(
        f"{url}/2026-11-16", json={"start_hour": 7, "duration_hours": 3}, headers=alice
    )

    assert added.status_code == HTTPStatus.OK
    assert added.json() == {"day": "2026-11-20", "start_hour": 18, "duration_hours": 2, **NEW_DAY}
    assert set(replaced.json()) == DAY_FIELDS
    current = (await isolated_client.get(f"{TASKS_URL}/{task['id']}", headers=alice)).json()
    assert [entry["day"] for entry in current["days"]] == ["2026-11-16", "2026-11-17", "2026-11-20"]
    assert current["days"][0] == {
        "day": "2026-11-16",
        "start_hour": 7,
        "duration_hours": 3,
        **NEW_DAY,
    }
    for day, body in [
        ("2026-11-21", {"start_hour": 22, "duration_hours": 3}),
        ("2026-11-21", {"start_hour": 8}),
        ("2026-11-21", {"start_hour": 8, "duration_hours": 1, "user_id": "x"}),
        ("2101-01-01", {"start_hour": 8, "duration_hours": 1}),
        ("pas-une-date", {"start_hour": 8, "duration_hours": 1}),
    ]:
        response = await isolated_client.put(f"{url}/{day}", json=body, headers=alice)
        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY, (day, body)


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : retire des jours, dans une transaction annulée à la fin du test.
# ensures  : un jour se retire ; le dernier jour est refusé en 409 et reste en place ; un jour
#            absent répond 404.
async def test_remove_task_day(isolated_client: OutboxClient, alice: dict[str, str]) -> None:
    task = await create_task(isolated_client, alice)
    url = f"{TASKS_URL}/{task['id']}/days"

    first = await isolated_client.delete(f"{url}/2026-11-16", headers=alice)
    last = await isolated_client.delete(f"{url}/2026-11-17", headers=alice)
    absent = await isolated_client.delete(f"{url}/2026-11-30", headers=alice)

    assert first.status_code == HTTPStatus.NO_CONTENT
    assert last.status_code == HTTPStatus.CONFLICT
    assert absent.status_code == HTTPStatus.NOT_FOUND
    current = (await isolated_client.get(f"{TASKS_URL}/{task['id']}", headers=alice)).json()
    assert [entry["day"] for entry in current["days"]] == ["2026-11-17"]


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée un compte et une tâche réellement validés, supprimés à la fin du test.
# ensures  : fournit le client, l'en-tête d'authentification et l'identifiant de la tâche.
@pytest.fixture
async def committed_task(
    database_client: OutboxClient, app_engine: AsyncEngine
) -> AsyncIterator[tuple[OutboxClient, dict[str, str], str]]:
    email = f"jours-{uuid.uuid4().hex[:12]}@exemple.org"
    account_id = await create_account(database_client, email=email)
    try:
        headers = bearer((await login_tokens(database_client, email=email))["access_token"])
        task = await create_task(database_client, headers)
        yield database_client, headers, str(task["id"])
    finally:
        # Suppression ciblée du seul compte créé ; ses tâches et jours suivent par cascade.
        async with app_engine.begin() as connection:
            await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": account_id})


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : retire simultanément les deux jours d'une tâche, sur deux connexions réelles.
# ensures  : le verrou de ligne sérialise les retraits : un seul réussit, l'autre est refusé en
#            409, et la tâche garde exactement un jour.
async def test_concurrent_day_removals_keep_one_day(
    committed_task: tuple[OutboxClient, dict[str, str], str],
) -> None:
    client, headers, task_id = committed_task
    url = f"{TASKS_URL}/{task_id}/days"

    responses = await asyncio.gather(
        client.delete(f"{url}/2026-11-16", headers=headers),
        client.delete(f"{url}/2026-11-17", headers=headers),
    )

    assert sorted(r.status_code for r in responses) == [HTTPStatus.NO_CONTENT, HTTPStatus.CONFLICT]
    remaining = (await client.get(f"{TASKS_URL}/{task_id}", headers=headers)).json()["days"]
    assert len(remaining) == 1
