# Backend rules (FastAPI)

## Layout
```
backend/
  app/
    main.py            # create_app(), lifespan (db engine, redis, metrics server :9101), routers, middleware
    config.py          # pydantic-settings Settings (env vars only; see ARCHITECTURE §9.1)
    db.py              # async engine/sessionmaker, get_db dependency
    models/            # SQLAlchemy 2.0 typed models
    schemas/           # Pydantic request/response models
    api/               # routers: auth, links, analytics, api_keys, health, redirect
    services/          # business logic — routers stay thin
    core/              # security (argon2 + semaphore), sessions, csrf+origin, rate_limit (Lua), redis (timeouts), metrics, logging, shortcode, urls, client_ip, host_guard
    worker/            # __main__.py (run), consumer.py, ua.py
  alembic/ alembic.ini
  tests/               # unit/ and integration/
  requirements.txt  requirements-dev.txt  Dockerfile  .dockerignore
```

## Rules
- Everything async. Never block the event loop: run argon2 hash/verify via `asyncio.to_thread` behind `asyncio.Semaphore(2)` (D10).
- Routers: validate → call service → return schema. No SQL in routers.
- Dependencies: `get_db`, `get_redis`, `current_user` (session OR API key), `csrf_protect` (token + Origin check).
- `destination_url` validation lives in ONE function (`core/urls.py`): http/https only, ≤ 2048 chars, valid host, not the `SHORT_URL_BASE` host, no `javascript:`/`data:`.
- Short code: 7-char base62 via `secrets.choice`; unique constraint; retry up to 5× on `IntegrityError`. Alias rules in PRD F2; reserved list in ARCHITECTURE §4.
- Redirect handler: follow the table in ARCHITECTURE §2.1 literally (timeouts, per-step fallbacks, metrics `redis_errors_total{op}`). Status precedence: disabled → 404, else expired → 410.
- Host guard middleware: if `PUBLIC_REDIRECT_HOST` is set and request `Host` equals it, serve only `GET`/`HEAD /{code}`; everything else 404. HEAD is an explicit route and never enqueues a click (D12).
- Client IP: `core/client_ip.py` implements the `TRUSTED_PROXY_COUNT` rule (ARCHITECTURE §6.3). No other code reads `X-Forwarded-For`.
- On link PATCH/DELETE/create: `DEL link:{code}` and `DEL neg:{code}` AFTER commit. Cache TTL = `min(LINK_CACHE_TTL_SECONDS, seconds_to_expiry)` ±10% jitter.
- Rate-limit script = one Lua EVAL (atomic INCR + EXPIRE). Limits from env so load tests can raise them. Create limit is per USER.
- Sessions/CSRF/cookies per ARCHITECTURE §2.4 and §6.1. Malformed/expired/unknown session → 401 + clear cookies.
- Worker per ARCHITECTURE §2.3 (batch + per-event fallback + dead-letter; group created at `0`, recreated on NOGROUP; extended XPENDING for delivery counts; consumer = pod name; never DELCONSUMER with pending entries; NO replay command in v1). All `WORKER_*` knobs from env (ARCH §9.1). Expose `click_stream_lag`, `click_stream_pending`, `click_stream_length` gauges (XINFO GROUPS `lag` may be nil → fall back to XLEN).
- Logging: structured JSON to stdout (`ts, level, msg, request_id, path, status, duration_ms`), `X-Request-ID`. No PII/secrets.
- Metrics: names/buckets in ARCHITECTURE §7. Routes labelled by template. Metrics server on `METRICS_PORT` (9101); worker on 9100.
- `/healthz` = process alive. `/readyz` = Postgres reachable (required); Redis down → still 200 with `{"redis":"degraded"}`.
- DB pool: `DB_POOL_SIZE=3`, `DB_MAX_OVERFLOW=2`. Budget formula in ARCHITECTURE §9.2.
- Dates: daily buckets returned as `YYYY-MM-DD` strings (UTC); range semantics in ARCHITECTURE §3.1.
- Docs only when `ENABLE_DOCS=true`.
- API runs ONE uvicorn process per pod (D15). DB connections honour `DATABASE_SSL_MODE`/`DATABASE_SSL_CA_FILE` (verify-full on RDS, D14). Create-link limiter fails closed (D13).
- `total_clicks`: one grouped query per page over `link_daily_stats`, never per-link queries (D17).
- Comment non-obvious code with WHY: the human must be able to explain every flow (redirect, worker, sessions) in an interview.

## Tests
- pytest + pytest-asyncio + httpx.AsyncClient against the real app, real Postgres, real Redis (separate test DB; flush test Redis DB between tests).
- Must cover: auth/session/CSRF+Origin flows, link CRUD + ownership isolation, PATCH null/omitted semantics, status precedence, redirect (hit/miss/negative/expired/disabled), rate limits incl. Redis stopped (fail-open redirect, fail-closed login), spoofed `X-Forwarded-For` ignored, host guard, worker idempotency (replay twice → one row, +1 count), batch with one orphan (others still stored), pending reclaim, dead-letter only-ack-after-success, commit-then-crash-before-XACK redelivery (one row, no rollup double count), NOGROUP recovery, HEAD never counted, API keys, argon2 defaults, UTC range zero-fill, cache race expiry within TTL.
- Ruff config in `pyproject.toml`; `ruff check` and `ruff format --check` must pass.
