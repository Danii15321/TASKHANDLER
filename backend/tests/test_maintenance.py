"""Tests de la purge : les durées de conservation sont appliquées, et rien d'actif n'est touché."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.maintenance import purge_expired_data
from tests.conftest import LogReader, act_as, unreachable_database_url

pytestmark = pytest.mark.anyio

VALID_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$aGFzaA"


# requires : `session` est une session isolée.
# assigns  : insère un compte, vérifié ou non, créé il y a `age` jours.
# ensures  : renvoie l'identifiant du compte.
async def account(session: AsyncSession, email: str, *, verified: bool, age: int) -> str:
    account_id: str = (
        await session.execute(
            text(
                "INSERT INTO users (email, password_hash, created_at, email_verified_at) VALUES "
                "(:e, :h, now() - make_interval(days => :age), "
                "CASE WHEN :verified THEN now() END) RETURNING id"
            ),
            {"e": email, "h": VALID_HASH, "age": age, "verified": verified},
        )
    ).scalar_one()
    return account_id


# requires : `session` est une session isolée, `user_id` un compte existant.
# assigns  : insère un jeton de session révoqué ou expiré depuis `age` jours, ou encore actif.
# ensures  : le jeton existe en base.
async def session_token(
    session: AsyncSession, user_id: str, marker: str, *, revoked_days_ago: int | None
) -> None:
    await session.execute(
        text(
            "INSERT INTO refresh_tokens (user_id, family_id, token_hash, created_at, expires_at, "
            "session_expires_at, revoked_at, revocation_reason) VALUES (:u, gen_random_uuid(), "
            ":hash, now() - interval '100 days', now() + interval '1 day', "
            "now() + interval '1 day', "
            "CASE WHEN CAST(:days AS integer) IS NULL THEN NULL "
            "ELSE now() - make_interval(days => CAST(:days AS integer)) END, "
            "CASE WHEN CAST(:days AS integer) IS NULL THEN NULL ELSE 'logout' END)"
        ),
        {"u": user_id, "hash": marker * 64, "days": revoked_days_ago},
    )


# requires : `db_session` est une session isolée.
# assigns  : insère des données récentes et anciennes, purge, dans une transaction annulée.
# ensures  : seules les données au-delà de leur durée de conservation disparaissent ; une session
#            active, un compte vérifié, même ancien, et un compte récent restent en place.
async def test_purge_applies_retention_periods(
    db_session: AsyncSession, read_logs: LogReader
) -> None:
    verified_old = await account(db_session, "ancien@exemple.org", verified=True, age=400)
    unverified_recent = await account(db_session, "recent@exemple.org", verified=False, age=2)
    await account(db_session, "abandon@exemple.org", verified=False, age=8)
    await session_token(db_session, verified_old, "a", revoked_days_ago=31)
    await session_token(db_session, verified_old, "b", revoked_days_ago=29)
    await session_token(db_session, verified_old, "c", revoked_days_ago=None)
    await db_session.execute(
        text(
            "INSERT INTO one_time_tokens (user_id, purpose, token_hash, created_at, expires_at, "
            "used_at) VALUES "
            "(:u, 'password_reset', :old, now() - interval '9 days', "
            "now() - interval '9 days' + interval '30 minutes', now() - interval '8 days'), "
            "(:u, 'email_verification', :fresh, now(), now() + interval '1 day', NULL)"
        ),
        {"u": unverified_recent, "old": "d" * 64, "fresh": "e" * 64},
    )
    await act_as(db_session, verified_old)
    settings = Settings(environment="test", database_url=unreachable_database_url(), _env_file=None)

    report = await purge_expired_data(db_session, settings)

    assert (report.sessions, report.one_time_tokens, report.unverified_accounts) == (1, 1, 1)
    emails = (await db_session.execute(text("SELECT email FROM users ORDER BY email"))).scalars()
    assert list(emails) == ["ancien@exemple.org", "recent@exemple.org"]
    hashes = await db_session.execute(text("SELECT left(token_hash, 1) FROM refresh_tokens"))
    assert sorted(hashes.scalars()) == ["b", "c"]
    remaining = (await db_session.execute(text("SELECT purpose FROM one_time_tokens"))).scalars()
    assert list(remaining) == ["email_verification"]
    events = [event for event in read_logs() if event["message"] == "retention_purge"]
    assert len(events) == 1
    counts = {
        key: events[0][key] for key in ("sessions", "single_use_links", "unverified_accounts")
    }
    assert counts == {"sessions": 1, "single_use_links": 1, "unverified_accounts": 1}
    assert "exemple.org" not in str(read_logs())
