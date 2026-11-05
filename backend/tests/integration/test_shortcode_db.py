"""Collision retry against the REAL database: the UNIQUE constraint is what decides who wins."""

import uuid
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.shortcode import (
    CodeGenerationError,
    is_code_collision,
    with_unique_code,
)
from app.db import create_engine, create_sessionmaker
from app.models import Link
from tests.integration.factories import make_link, make_user


def factory_from(codes: list[str]) -> tuple[list[str], Callable[[], str]]:
    """A code factory that hands out `codes` in order and records what it handed out."""
    handed_out: list[str] = []
    it: Iterator[str] = iter(codes)

    def factory() -> str:
        code = next(it)
        handed_out.append(code)
        return code

    return handed_out, factory


async def test_collision_retry(db: AsyncSession) -> None:
    user = await make_user(db)
    await make_link(db, user, "TAKEN01")
    await make_link(db, user, "TAKEN02")
    handed_out, factory = factory_from(["TAKEN01", "TAKEN02", "FREE001"])

    engine = create_engine(Settings())
    sessionmaker = create_sessionmaker(engine)

    async def insert(code: str) -> str:
        # A fresh session per attempt: a failed INSERT leaves its transaction unusable.
        async with sessionmaker() as session:
            session.add(Link(user_id=user.id, code=code, destination_url="https://example.com"))
            await session.commit()
        return code

    try:
        result = await with_unique_code(insert, code_factory=factory)
    finally:
        await engine.dispose()

    assert result == "FREE001"
    assert handed_out == ["TAKEN01", "TAKEN02", "FREE001"]  # two collisions, then success


async def test_collision_retry_gives_up_after_max_attempts(db: AsyncSession) -> None:
    user = await make_user(db)
    await make_link(db, user, "TAKEN01")
    handed_out, factory = factory_from(["TAKEN01"] * 5)

    engine = create_engine(Settings())
    sessionmaker = create_sessionmaker(engine)

    async def insert(code: str) -> str:
        async with sessionmaker() as session:
            session.add(Link(user_id=user.id, code=code, destination_url="https://example.com"))
            await session.commit()
        return code

    try:
        with pytest.raises(CodeGenerationError):
            await with_unique_code(insert, code_factory=factory, max_attempts=5)
    finally:
        await engine.dispose()
    assert len(handed_out) == 5  # exactly the attempt budget, no infinite loop


async def test_other_integrity_errors_are_not_retried(db: AsyncSession) -> None:
    """A foreign-key failure is a bug, not a collision: it must surface immediately."""
    handed_out, factory = factory_from(["Abc1234", "Def5678"])

    async def insert(code: str) -> str:
        db.add(Link(user_id=uuid.uuid4(), code=code, destination_url="https://example.com"))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise
        return code

    with pytest.raises(IntegrityError) as exc:
        await with_unique_code(insert, code_factory=factory)
    assert not is_code_collision(exc.value)
    assert handed_out == ["Abc1234"]  # no second attempt


async def test_is_code_collision_identifies_the_constraint(db: AsyncSession) -> None:
    user = await make_user(db)
    await make_link(db, user, "Same123")
    with pytest.raises(IntegrityError) as exc:
        await make_link(db, user, "Same123")
    assert is_code_collision(exc.value)
