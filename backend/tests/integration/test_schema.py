"""Schema tests: the migration, and the constraints the application relies on (ARCHITECTURE §3)."""

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, ClickEvent, Link, LinkDailyStat, User
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import BACKEND_DIR, plain_dsn, run_alembic
from tests.integration.factories import make_link, make_user, new_api_key, new_click


async def count(db: AsyncSession, model: type) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# --- the migration itself --------------------------------------------------------------------


async def test_upgrade_on_empty_db() -> None:
    """A brand-new empty database reaches the full schema with one `alembic upgrade head`."""
    name = f"linkforge_empty_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(plain_dsn(TEST_DATABASE_URL, "postgres"), timeout=5)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        url = make_url(TEST_DATABASE_URL).set(database=name).render_as_string(hide_password=False)

        result = await asyncio.to_thread(run_alembic, "upgrade", "head", database_url=url)
        assert result.returncode == 0, result.stderr

        conn = await asyncpg.connect(plain_dsn(url), timeout=5)
        try:
            tables = {
                r["tablename"]
                for r in await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
            }
            indexes = {
                r["indexname"]
                for r in await conn.fetch(
                    "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
                )
            }
        finally:
            await conn.close()
        assert tables == {
            "alembic_version",
            "users",
            "links",
            "click_events",
            "link_daily_stats",
            "api_keys",
        }
        assert {
            "uq_users_email_lower",
            "ix_links_user_id_created_at",
            "ix_click_events_link_id_clicked_at",
        } <= indexes
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def test_models_match_migration() -> None:
    """`alembic check` fails if the models and the migration have drifted apart."""
    result = await asyncio.to_thread(run_alembic, "check", database_url=TEST_DATABASE_URL)
    assert result.returncode == 0, result.stdout + result.stderr


async def test_a_migration_needs_nothing_but_the_database_url() -> None:
    """The migration Job must not need the application's other settings (D72): a real alembic
    process whose whole environment is PATH and DATABASE_URL (no IP_HASH_SECRET, no REDIS_URL)."""
    env = {"PATH": os.environ["PATH"], "DATABASE_URL": TEST_DATABASE_URL}
    for command in (["current"], ["upgrade", "head"], ["check"]):
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", *command],
            cwd=BACKEND_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (command, result.stdout + result.stderr)


# --- uniqueness -------------------------------------------------------------------------------


async def test_unique_email_ci(db: AsyncSession) -> None:
    await make_user(db, "Alice@Example.com")
    with pytest.raises(IntegrityError) as exc:
        await make_user(db, "alice@example.COM")
    assert "uq_users_email_lower" in str(exc.value)


async def test_link_code_unique_and_case_sensitive(db: AsyncSession) -> None:
    user = await make_user(db)
    await make_link(db, user, "Docs")
    await make_link(db, user, "docs")  # different case = a different code (PRD F2)
    with pytest.raises(IntegrityError) as exc:
        await make_link(db, user, "Docs")
    assert "uq_links_code" in str(exc.value)


async def test_event_id_unique(db: AsyncSession) -> None:
    link = await make_link(db, await make_user(db))
    event_id = uuid.uuid4()
    db.add(new_click(link, event_id))
    await db.commit()
    db.add(new_click(link, event_id))
    with pytest.raises(IntegrityError) as exc:
        await db.commit()
    assert "uq_click_events_event_id" in str(exc.value)


async def test_api_key_prefix_unique(db: AsyncSession) -> None:
    user = await make_user(db)
    db.add(new_api_key(user, "ab12cd34"))
    await db.commit()
    db.add(new_api_key(user, "ab12cd34"))
    with pytest.raises(IntegrityError) as exc:
        await db.commit()
    assert "uq_api_keys_prefix" in str(exc.value)


async def test_daily_stats_primary_key(db: AsyncSession) -> None:
    link = await make_link(db, await make_user(db))
    db.add(LinkDailyStat(link_id=link.id, day=date(2026, 11, 5), clicks=1))
    await db.commit()
    db.add(LinkDailyStat(link_id=link.id, day=date(2026, 11, 5), clicks=1))
    with pytest.raises(IntegrityError) as exc:
        await db.commit()
    assert "pk_link_daily_stats" in str(exc.value)


# --- foreign keys -----------------------------------------------------------------------------


async def test_click_event_requires_existing_link(db: AsyncSession) -> None:
    db.add(
        ClickEvent(
            event_id=uuid.uuid4(),
            link_id=uuid.uuid4(),  # no such link
            clicked_at=datetime.now(UTC),
            ip_hash="h",
        )
    )
    with pytest.raises(IntegrityError) as exc:
        await db.commit()
    assert "fk_click_events_link_id_links" in str(exc.value)


async def _populate(db: AsyncSession) -> tuple[User, Link]:
    user = await make_user(db)
    link = await make_link(db, user)
    db.add(new_click(link))
    db.add(LinkDailyStat(link_id=link.id, day=date(2026, 11, 5), clicks=1))
    db.add(new_api_key(user))
    await db.commit()
    return user, link


async def test_fk_cascade_delete_user(db: AsyncSession) -> None:
    """Deleting a user removes everything that hangs off them."""
    user, _ = await _populate(db)
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()
    for model in (User, Link, ClickEvent, LinkDailyStat, ApiKey):
        assert await count(db, model) == 0, model.__name__


async def test_fk_cascade_delete_link(db: AsyncSession) -> None:
    """Deleting a link removes its click data but keeps the owner and their API keys."""
    _, link = await _populate(db)
    await db.execute(delete(Link).where(Link.id == link.id))
    await db.commit()
    assert await count(db, Link) == 0
    assert await count(db, ClickEvent) == 0
    assert await count(db, LinkDailyStat) == 0
    assert await count(db, User) == 1
    assert await count(db, ApiKey) == 1


# --- server-side defaults ---------------------------------------------------------------------


async def test_server_defaults(db: AsyncSession) -> None:
    user = await make_user(db)
    link = await make_link(db, user)
    db.add(new_click(link))
    db.add(LinkDailyStat(link_id=link.id, day=date(2026, 11, 5)))
    await db.commit()

    await db.refresh(user)
    await db.refresh(link)
    assert user.is_active is True and user.created_at is not None
    assert link.is_active is True and link.created_at is not None and link.updated_at is not None
    assert link.title is None and link.expires_at is None

    click = (await db.execute(select(ClickEvent))).scalar_one()
    assert click.is_bot is False and click.id == 1  # identity column starts at 1 (table was reset)
    stat = (await db.execute(select(LinkDailyStat))).scalar_one()
    assert stat.clicks == 0
