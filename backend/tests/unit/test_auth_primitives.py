"""Email rules and the password service (task P2.4). No Redis or Postgres needed."""

import asyncio
import threading
import time

import pytest
from argon2 import PasswordHasher
from argon2.profiles import RFC_9106_LOW_MEMORY

from app.core.emails import normalize_email
from app.core.passwords import PasswordService

# --- emails -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("user@example.com", "user@example.com"),
        ("  User@Example.COM  ", "user@example.com"),  # trimmed and lowercased (PRD F1)
        ("first.last+tag@sub.example.co.uk", "first.last+tag@sub.example.co.uk"),
        ("a@b.io", "a@b.io"),
    ],
)
def test_valid_emails_are_normalised(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "plainaddress",
        "@example.com",
        "user@",
        "user@example",  # no dot in the domain
        "user@@example.com",
        "us er@example.com",
        "user@exa mple.com",
        "user@.example.com",
        "user@example..com",
        ".user@example.com",
        "user.@example.com",
        "user@-example.com",
        "user@example-.com",
        "ünïcode@example.com",  # ASCII only in v1
        "user@exämple.com",
        "user@example.com\n",  # stripped, so this one is actually fine; see next line
    ][:-1],
)
def test_invalid_emails_are_rejected(raw: str) -> None:
    assert normalize_email(raw) is None


def test_email_length_limits() -> None:
    assert normalize_email("a" * 64 + "@example.com") is not None
    assert normalize_email("a" * 65 + "@example.com") is None  # local part over 64
    long_domain = ".".join(["a" * 60] * 5) + ".com"  # 5 labels of 60 + dots: over 254 in total
    assert len("x@" + long_domain) > 254
    assert normalize_email("x@" + long_domain) is None
    assert normalize_email("x@" + "a" * 64 + ".com") is None  # label over 63


# --- argon2 defaults are what the architecture says -------------------------------------------


def test_password_hasher_uses_the_rfc9106_low_memory_profile() -> None:
    """ARCHITECTURE §6.2: library defaults, asserted so a library upgrade cannot silently
    weaken the hash (t=3, m=64 MiB, p=4, argon2id)."""
    params = PasswordService().hasher._parameters
    assert params == RFC_9106_LOW_MEMORY
    assert (params.time_cost, params.memory_cost, params.parallelism) == (3, 65536, 4)


async def test_hash_is_argon2id_with_those_parameters_and_verifies() -> None:
    svc = PasswordService()
    hashed = await svc.hash("correct horse battery")
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert "correct horse" not in hashed
    assert await svc.verify(hashed, "correct horse battery") is True
    assert await svc.verify(hashed, "wrong horse battery") is False


@pytest.mark.parametrize("stored", ["!", "not-a-real-hash", "", "$argon2id$garbage"])
async def test_verify_is_false_for_values_that_are_not_hashes(stored: str) -> None:
    # The dev user ("!") and test placeholders must never raise, and never match.
    assert await PasswordService().verify(stored, "anything at all") is False


async def test_verify_unknown_costs_about_a_real_verification() -> None:
    svc = PasswordService()
    real = await svc.hash("some real password")
    started = time.perf_counter()
    await svc.verify(real, "some real password")
    real_cost = time.perf_counter() - started

    await svc.verify_unknown("x")  # first call also builds the dummy hash
    started = time.perf_counter()
    await svc.verify_unknown("x")
    unknown_cost = time.perf_counter() - started
    # Same order of magnitude: far from a no-op, so response time does not reveal "no such email".
    assert unknown_cost > real_cost * 0.4


# --- the semaphore ----------------------------------------------------------------------------


class _CountingHasher(PasswordHasher):
    """Records how many hashes run at the same moment."""

    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def hash(self, password, *, salt=None):  # type: ignore[no-untyped-def,override]
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.05)
            return "$fake"
        finally:
            with self._lock:
                self.active -= 1


async def test_at_most_two_hashes_run_at_once() -> None:
    """D10: 2 x 64 MiB is what the API pod's memory limit is sized for."""
    hasher = _CountingHasher()
    svc = PasswordService(concurrency=2, hasher=hasher)
    await asyncio.gather(*(svc.hash("x" * 12) for _ in range(8)))
    assert hasher.peak == 2
