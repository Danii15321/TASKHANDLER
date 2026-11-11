"""Matrice de sécurité des tâches, exigée par CLAUDE.md pour **chaque** route protégée.

- sans jeton, avec un jeton expiré ou falsifié : `401` ;
- ressource d'un autre utilisateur, en lecture, modification et suppression : `404`, données
  intactes (critère de fin de la phase 8) ;
- entrée invalide, champ inconnu ou `user_id` injecté : `422` ;
- réponse sans aucun champ sensible.
"""

import secrets
import uuid
from http import HTTPStatus
from typing import Any

import jwt
import pytest

from tests.conftest import TEST_JWT_SECRET, OutboxClient, bearer, create_account, login_tokens

pytestmark = pytest.mark.anyio

TASKS_URL = "/api/v1/tasks"
BOB = "bob@exemple.org"
SENSITIVE_MARKERS = ("user_id", "password", "hash", "token", "email")

# Chaque route protégée, avec un corps valide : seule l'identité du demandeur change d'un cas à
# l'autre. `{id}` est remplacé par la tâche d'Alice.
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", f"{TASKS_URL}/{{id}}", None),
    ("PATCH", f"{TASKS_URL}/{{id}}", {"title": "Piraté"}),
    ("DELETE", f"{TASKS_URL}/{{id}}", None),
    ("PUT", f"{TASKS_URL}/{{id}}/days/2026-11-16", {"start_hour": 3, "duration_hours": 1}),
    ("PUT", f"{TASKS_URL}/{{id}}/days/2026-11-25", {"start_hour": 3, "duration_hours": 1}),
    ("DELETE", f"{TASKS_URL}/{{id}}/days/2026-11-16", None),
]
ROUTE_IDS = ["lire", "modifier", "supprimer", "remplacer-jour", "ajouter-jour", "retirer-jour"]
VALID_TASK = {
    "title": "Tâche d'Alice",
    "description": "Description privée d'Alice",
    "days": [
        {"day": "2026-11-16", "start_hour": 9, "duration_hours": 1},
        {"day": "2026-11-17", "start_hour": 9, "duration_hours": 1},
    ],
}


# requires : `client` est relié à l'application de test.
# assigns  : crée un compte vérifié et ouvre une session.
# ensures  : renvoie l'en-tête d'authentification et le jeton d'accès de ce compte.
async def session_for(client: OutboxClient, email: str) -> tuple[dict[str, str], str]:
    await create_account(client, email=email)
    access = (await login_tokens(client, email=email))["access_token"]
    return bearer(access), access


# requires : `isolated_client` est relié à l'application de test.
# assigns  : crée Alice et sa tâche, dans une transaction annulée à la fin du test.
# ensures  : fournit l'en-tête d'Alice, son jeton d'accès et sa tâche.
@pytest.fixture
async def alice_task(
    isolated_client: OutboxClient,
) -> tuple[dict[str, str], str, dict[str, Any]]:
    headers, access = await session_for(isolated_client, "alice@exemple.org")
    response = await isolated_client.post(TASKS_URL, json=VALID_TASK, headers=headers)
    assert response.status_code == HTTPStatus.CREATED
    return headers, access, response.json()


# requires : `client` est relié à l'application, `method` et `url` désignent une route.
# assigns  : envoie la requête.
# ensures  : renvoie le code HTTP de la réponse.
async def call(
    client: OutboxClient,
    method: str,
    url: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> int:
    response = await client.request(method, url, json=body, headers=headers)
    return response.status_code


# requires : `method`, `url` et `body` décrivent une route protégée.
# assigns  : envoie la requête de Bob sur la tâche d'Alice, dans une transaction annulée.
# ensures  : Bob reçoit 404, jamais 403, et la tâche d'Alice est strictement inchangée.
@pytest.mark.parametrize(("method", "url", "body"), ROUTES, ids=ROUTE_IDS)
async def test_other_user_gets_not_found_and_data_is_intact(
    isolated_client: OutboxClient,
    alice_task: tuple[dict[str, str], str, dict[str, Any]],
    method: str,
    url: str,
    body: dict[str, Any] | None,
) -> None:
    alice, _, task = alice_task
    bob, _ = await session_for(isolated_client, BOB)

    status = await call(isolated_client, method, url.format(id=task["id"]), body, bob)

    assert status == HTTPStatus.NOT_FOUND
    unchanged = await isolated_client.get(f"{TASKS_URL}/{task['id']}", headers=alice)
    assert unchanged.json() == task


# requires : `method`, `url` et `body` décrivent une route protégée.
# assigns  : envoie la requête sans jeton valide.
# ensures  : sans jeton, avec un jeton expiré, falsifié ou d'un autre algorithme : toujours 401,
#            et la tâche est inchangée.
@pytest.mark.parametrize(("method", "url", "body"), ROUTES, ids=ROUTE_IDS)
async def test_route_requires_valid_token(
    isolated_client: OutboxClient,
    alice_task: tuple[dict[str, str], str, dict[str, Any]],
    method: str,
    url: str,
    body: dict[str, Any] | None,
) -> None:
    alice, access, task = alice_task
    claims = jwt.decode(access, options={"verify_signature": False})
    expired = {**claims, "iat": claims["iat"] - 3600, "exp": claims["exp"] - 3600}
    target = url.format(id=task["id"])

    for headers in [
        {},
        bearer(jwt.encode(expired, TEST_JWT_SECRET, algorithm="HS256")),
        bearer(jwt.encode(claims, secrets.token_hex(32), algorithm="HS256")),
        bearer(jwt.encode(claims, TEST_JWT_SECRET, algorithm="HS512")),
    ]:
        assert await call(isolated_client, method, target, body, headers) == HTTPStatus.UNAUTHORIZED

    unchanged = await isolated_client.get(f"{TASKS_URL}/{task['id']}", headers=alice)
    assert unchanged.json() == task


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée une tâche sans jeton, dans une transaction annulée à la fin du test.
# ensures  : la création exige elle aussi un jeton valide.
async def test_create_requires_valid_token(isolated_client: OutboxClient) -> None:
    response = await isolated_client.post(TASKS_URL, json=VALID_TASK)

    assert response.status_code == HTTPStatus.UNAUTHORIZED


# requires : `extra` est un champ que le client ne doit jamais pouvoir fixer.
# assigns  : tente d'injecter le champ, dans une transaction annulée à la fin du test.
# ensures  : l'injection de `user_id` (ou de tout champ inconnu) est refusée en 422, à la création
#            comme à la modification et sur un jour.
@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": "00000000-0000-0000-0000-000000000001"},
        {"id": "00000000-0000-0000-0000-000000000001"},
        {"created_at": "2026-01-01T00:00:00Z"},
        {"priority": "high"},
    ],
)
async def test_injected_fields_are_refused(
    isolated_client: OutboxClient,
    alice_task: tuple[dict[str, str], str, dict[str, Any]],
    extra: dict[str, Any],
) -> None:
    alice, _, task = alice_task
    targets = [
        ("POST", TASKS_URL, {**VALID_TASK, **extra}),
        ("PATCH", f"{TASKS_URL}/{task['id']}", {"title": "x", **extra}),
        (
            "PUT",
            f"{TASKS_URL}/{task['id']}/days/2026-11-20",
            {"start_hour": 1, "duration_hours": 1, **extra},
        ),
    ]

    for method, url, body in targets:
        assert await call(isolated_client, method, url, body, alice) == (
            HTTPStatus.UNPROCESSABLE_ENTITY
        )


# requires : `path` contient un identifiant ou un jour invalide.
# assigns  : rien.
# ensures  : un identifiant ou un jour mal formé dans l'URL est refusé en 422.
@pytest.mark.parametrize(
    "path",
    [
        f"{TASKS_URL}/pas-un-uuid",
        f"{TASKS_URL}/1",
        f"{TASKS_URL}/{uuid.uuid4()}/days/2026-13-01",
        f"{TASKS_URL}/{uuid.uuid4()}/days/1999-12-31",
    ],
)
async def test_malformed_path_is_refused(
    isolated_client: OutboxClient,
    alice_task: tuple[dict[str, str], str, dict[str, Any]],
    path: str,
) -> None:
    alice, _, _ = alice_task

    response = await isolated_client.get(path, headers=alice)

    assert response.status_code in {HTTPStatus.UNPROCESSABLE_ENTITY, HTTPStatus.METHOD_NOT_ALLOWED}
    assert response.status_code != HTTPStatus.INTERNAL_SERVER_ERROR


# requires : PostgreSQL et Redis sont démarrés.
# assigns  : crée, modifie et lit une tâche, dans une transaction annulée à la fin du test.
# ensures  : aucune réponse ne contient de champ sensible : ni `user_id`, ni donnée de compte.
async def test_responses_contain_no_sensitive_field(
    isolated_client: OutboxClient,
    alice_task: tuple[dict[str, str], str, dict[str, Any]],
) -> None:
    alice, _, task = alice_task
    responses = [
        await isolated_client.get(f"{TASKS_URL}/{task['id']}", headers=alice),
        await isolated_client.patch(
            f"{TASKS_URL}/{task['id']}", json={"title": "B"}, headers=alice
        ),
        await isolated_client.put(
            f"{TASKS_URL}/{task['id']}/days/2026-11-20",
            json={"start_hour": 1, "duration_hours": 1},
            headers=alice,
        ),
    ]

    for response in responses:
        assert response.status_code == HTTPStatus.OK
        for marker in SENSITIVE_MARKERS:
            assert marker not in response.text, (marker, response.text)
