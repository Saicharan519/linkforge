"""Building a click event (task P3.1): hashing, referrer host, field set. No Redis needed."""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.clicks import (
    REFERRER_HOST_MAX,
    USER_AGENT_MAX,
    build_event,
    hash_ip,
    referrer_host,
)

SECRET = "a-test-secret-of-sufficient-length"
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
LINK_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def settings(secret: str = SECRET) -> Settings:
    return Settings(database_url="postgresql+asyncpg://u:p@h/d", ip_hash_secret=secret)


# --- the IP hash ------------------------------------------------------------------------------


def test_ip_hash_is_hmac_sha256_hex_of_the_address() -> None:
    expected = hmac.new(SECRET.encode(), b"203.0.113.7", hashlib.sha256).hexdigest()
    assert hash_ip("203.0.113.7", SECRET) == expected
    assert len(expected) == 64


def test_ip_hash_is_stable_per_secret_and_differs_between_secrets_and_addresses() -> None:
    assert hash_ip("1.2.3.4", SECRET) == hash_ip("1.2.3.4", SECRET)  # countable per visitor
    assert hash_ip("1.2.3.4", SECRET) != hash_ip("1.2.3.5", SECRET)
    assert hash_ip("1.2.3.4", SECRET) != hash_ip("1.2.3.4", SECRET + "x")  # keyed
    assert "1.2.3.4" not in hash_ip("1.2.3.4", SECRET)


def test_ip_hash_secret_is_required_and_must_be_long_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IP_HASH_SECRET", raising=False)
    base = {"database_url": "postgresql+asyncpg://u:p@h/d"}
    with pytest.raises(ValidationError):
        Settings(**base)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        Settings(**base, ip_hash_secret="short")  # type: ignore[arg-type]
    Settings(**base, ip_hash_secret="x" * 16)  # type: ignore[arg-type]


# --- the referrer: host only ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("referer", "host"),
    [
        ("https://news.example.com/article?session=SECRET#top", "news.example.com"),
        ("http://User:pw@Example.COM:8080/a/b?c=d", "example.com"),  # no userinfo, port or case
        ("https://[2001:db8::1]:443/path", "2001:db8::1"),
        ("android-app://com.google.android.gm", "com.google.android.gm"),
        ("https://example.com", "example.com"),
        ("  https://example.com/x  ", "example.com"),
    ],
)
def test_referrer_keeps_only_the_host(referer: str, host: str) -> None:
    assert referrer_host(referer) == host


@pytest.mark.parametrize(
    "referer", [None, "", "   ", "not a url", "/relative/path", "mailto:a@b.c", "http://[bad"]
)
def test_a_missing_or_unusable_referrer_is_empty(referer: str | None) -> None:
    assert referrer_host(referer) == ""


def test_the_referrer_path_and_query_never_survive() -> None:
    host = referrer_host("https://search.example/?q=my+private+query&token=abc123")
    assert host == "search.example"
    assert "private" not in host and "abc123" not in host


def test_an_absurdly_long_referrer_host_is_capped() -> None:
    assert len(referrer_host("https://" + "a" * 1000 + ".example/")) == REFERRER_HOST_MAX


# --- the whole event --------------------------------------------------------------------------


def test_event_has_exactly_the_documented_string_fields() -> None:
    event = build_event(
        settings=settings(),
        link_id=LINK_ID,
        code="abc1234",
        ip="203.0.113.7",
        user_agent="Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0",
        referer="https://news.example.com/a",
        now=NOW,
    )
    assert set(event) == {
        "event_id",
        "link_id",
        "code",
        "ts",
        "ip_hash",
        "user_agent",
        "referrer_host",
    }
    assert all(isinstance(v, str) for v in event.values())  # a stream entry is flat strings
    assert uuid.UUID(event["event_id"]).version == 4
    assert event["link_id"] == str(LINK_ID) and event["code"] == "abc1234"
    assert event["ts"] == "2026-11-06T12:00:00+00:00"
    assert event["ip_hash"] == hash_ip("203.0.113.7", SECRET)
    assert event["referrer_host"] == "news.example.com"
    assert "203.0.113.7" not in "".join(event.values())  # the raw address is nowhere


def test_every_event_gets_a_new_event_id() -> None:
    kwargs = dict(
        settings=settings(),
        link_id=LINK_ID,
        code="abc1234",
        ip="1.2.3.4",
        user_agent=None,
        referer=None,
        now=NOW,
    )
    assert build_event(**kwargs)["event_id"] != build_event(**kwargs)["event_id"]  # type: ignore[arg-type]


def test_user_agent_is_truncated_and_missing_becomes_empty() -> None:
    common = dict(
        settings=settings(), link_id=LINK_ID, code="abc1234", ip="1.2.3.4", referer=None, now=NOW
    )
    long = build_event(user_agent="U" * 2000, **common)  # type: ignore[arg-type]
    assert len(long["user_agent"]) == USER_AGENT_MAX == 512
    assert build_event(user_agent=None, **common)["user_agent"] == ""  # type: ignore[arg-type]
