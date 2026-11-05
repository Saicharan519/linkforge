"""Application settings.

Config comes ONLY from environment variables (pydantic-settings). Nothing is read from files
and no secrets have defaults. Names and defaults follow docs/ARCHITECTURE.md §9.1.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": the environment also holds variables for other services (e.g. POSTGRES_USER
    # for docker compose); those must not make the app fail to start.
    model_config = SettingsConfigDict(extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"

    # Required, no default: failing fast at startup beats silently connecting to a guessed database.
    database_url: str

    # Connection budget per API pod = pool + overflow (ARCHITECTURE §9.2).
    db_pool_size: int = 3
    db_max_overflow: int = 2

    # OpenAPI docs are served at /api/docs only when this is true (dev only).
    enable_docs: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
