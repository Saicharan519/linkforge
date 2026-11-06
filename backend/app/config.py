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

    # Redis (ARCHITECTURE §2.1, §9.1). The default is a convenience for local runs only; it is not
    # a secret and a wrong value is survivable by design: every Redis call has a fallback (D56).
    redis_url: str = "redis://localhost:6379/0"
    # Per-operation socket timeout and connect timeout. Short on purpose: a slow Redis must cost
    # the redirect path at most ~100 ms per call, never a hung request.
    redis_socket_timeout_ms: int = 100
    redis_connect_timeout_ms: int = 200

    # Upper bound for how long the redirect cache may serve a stale value (ARCHITECTURE §2.1.1).
    # Never longer than the link's own expiry; jittered +-10% but never above this value.
    link_cache_ttl_seconds: int = 300

    # Rate limits (ARCHITECTURE §5, §9.1). All from the environment so a load test can raise them.
    rate_limit_redirect_per_min: int = 120  # per client IP; fails open
    rate_limit_create_per_min: int = 30  # per user; fails closed (wired in P2.7)
    rate_limit_login_max: int = 5  # per IP+email per 15 minutes; fails closed (wired in P2.4)
    # How many reverse proxies of ours sit in front of the API; see core/client_ip.py.
    # 0 = none (local), 1 = Traefik on kind or the ALB on EKS.
    trusted_proxy_count: int = 0

    # Sessions and cookies (ARCHITECTURE §2.4, §6.1). Secure cookies need HTTPS: on in production.
    session_ttl_seconds: int = 604800  # 7 days, sliding
    cookie_secure: bool = False

    # OpenAPI docs are served at /api/docs only when this is true (dev only).
    enable_docs: bool = False

    # Public base of short links (e.g. https://go.example.com). Used to build `short_url` and to
    # refuse destinations that point back at our own short domain (redirect loops).
    short_url_base: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
