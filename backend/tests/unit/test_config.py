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


# --- the database settings are separate from the application settings (D72) -------------------


def test_database_settings_need_only_the_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import DatabaseSettings

    for name in ("IP_HASH_SECRET", "REDIS_URL", "ALLOWED_ORIGINS", "ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    assert DatabaseSettings().database_url == "postgresql+asyncpg://u:p@localhost/db"


def test_database_settings_still_require_the_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import DatabaseSettings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        DatabaseSettings()


def test_the_application_settings_stay_strict_about_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Splitting the classes must not loosen the application: no secret, no start."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.delenv("IP_HASH_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_the_application_settings_extend_the_database_settings() -> None:
    from app.config import DatabaseSettings

    assert issubclass(Settings, DatabaseSettings)
    assert "database_url" in DatabaseSettings.model_fields
    assert set(DatabaseSettings.model_fields) == {"database_url"}  # nothing else sneaks in
