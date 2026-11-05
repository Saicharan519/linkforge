import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_settings_defaults_match_architecture(monkeypatch: pytest.MonkeyPatch) -> None:
    # ARCHITECTURE §9.1: pool 3 + overflow 2 per API pod; docs off unless asked for.
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    s = Settings()
    assert (s.env, s.db_pool_size, s.db_max_overflow, s.enable_docs) == ("dev", 3, 2, False)


def test_settings_ignores_unrelated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # docker compose variables live in the same environment and must not break startup.
    monkeypatch.setenv("POSTGRES_USER", "whatever")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    Settings()
