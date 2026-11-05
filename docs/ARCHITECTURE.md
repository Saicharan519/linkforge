# LinkForge — Architecture (rev 2)

Source of truth for names, endpoints, keys, schema, metrics, and failure behaviour. If code and this file disagree, fix one of them deliberately. If this file is silent, STOP and ask (see root CLAUDE.md "no invention").

## 1. Big picture

```
                 app.DOMAIN                         go.DOMAIN
        (SPA + /api/*)                      (short links ONLY: GET /{code})
              |                                     |
        +-----+---------------- Ingress ------------+
        |                                           |
   frontend (nginx)                         api (FastAPI, 2-6 pods, HPA)
   serves SPA           /api/* ------------>  |   metrics on separate port :9101 (never in an Ingress)
                                              |
                  +---------------------------+---------------------+
                  |                           |                     |
              Postgres                      Redis  <---XADD---- (redirect path, bounded timeout)
         (RDS in cloud,                (cache, sessions,              |
          StatefulSet on kind)          rate limits, stream)          | XREADGROUP
                  ^                           |                       v
                  +-------- bulk insert ------+------------------  worker (same image)
                            + daily rollups                         metrics :9100
```
- One backend image, two commands: `uvicorn app.main:app` (API) and `python -m app.worker` (consumer). The API runs ONE uvicorn process per pod (scale with replicas; per-process metrics and the metrics server assume it).
- Local: API on :8000 serves `/api/*` and `/{code}` (`SHORT_URL_BASE=http://localhost:8000`). Vite dev server :5173 proxies `/api`. Compose prod-like UI on :8080 (nginx proxies `/api`).

### 1.1 Deployment topology (do NOT assume these are identical)
| Component | Compose (local) | kind | EKS |
|---|---|---|---|
| Frontend | container (nginx) | Deployment | Deployment |
| API | container | Deployment (+HPA, PDB) | Deployment (+HPA, PDB) |
| Worker | container | Deployment | Deployment |
| Migration | one-shot compose service | Argo Job (sync-wave hook) | Argo Job (sync-wave hook) |
| PostgreSQL | container | StatefulSet (in our chart) | **RDS** (chart `postgres.enabled=false`) |
| Redis | container (AOF on) | StatefulSet + PVC | StatefulSet + PVC (not ElastiCache: cost) |
| Ingress | nginx in frontend container | Traefik | AWS Load Balancer Controller (ALB) |
| Secrets | `.env` (gitignored) | Sealed Secrets | Sealed Secrets |
| Images | built locally | public **GHCR** (CI push, no AWS; `kind load` for local iteration) | **ECR** (nodes pull natively) |
| Helm values | n/a | `envs/kind/values.yaml` | `envs/eks/values.yaml` (never share one file) |
| Monitoring | optional | kube-prometheus-stack | kube-prometheus-stack |

### 1.2 Network exposure
| Surface | Reachable from | Notes |
|---|---|---|
| `app.DOMAIN/` → frontend, `app.DOMAIN/api/*` → API | internet | the only app-host routes |
| `go.DOMAIN/*` → API | internet | API middleware enforces: when `Host == PUBLIC_REDIRECT_HOST`, ONLY `GET`/`HEAD /{code}` is served; everything else (incl. `/api`, `/docs`, `/healthz`) → 404 |
| `/metrics` | in-cluster only | API serves it on `METRICS_PORT=9101`, worker on `:9100`; Services expose a port named `metrics`; no Ingress references it |
| `/healthz`, `/readyz` | kubelet probes (pod IP) | not reachable via `go` host; on `app` host only under `/api` is routed, so they are not public |
| Postgres 5432, Redis 6379 | in-cluster ClusterIP only | NetworkPolicies deliberately deferred (listed as "production would add"); RDS is in private subnets, SG allows only the node SG |
| Argo CD, Grafana, Prometheus | `kubectl port-forward` only | no Ingress |
| EKS API endpoint | your IP only | public endpoint restricted with `cluster_endpoint_public_access_cidrs = ["<your-ip>/32"]` (update when your IP changes) |

## 2. Request flows

### 2.1 Redirect `GET /{code}` — exact fallbacks
Redis client config: `socket_timeout = REDIS_SOCKET_TIMEOUT_MS` (default 100 ms), `socket_connect_timeout = REDIS_CONNECT_TIMEOUT_MS` (200 ms). Every Redis call on this path is wrapped; an exception NEVER becomes a 500.

| Step | Normal | If Redis errors/times out | Metric |
|---|---|---|---|
| 1 rate limit `rl:redirect:{ip}:{minute}` (`RATE_LIMIT_REDIRECT_PER_MIN`, default 120) | allow/429 | **allow (fail open)** | `redis_errors_total{op="ratelimit"}` |
| 2 `GET link:{code}` | hit → go to 6 | treat as miss, go to DB | `redis_errors_total{op="cache_get"}` |
| 3 `GET neg:{code}` | exists → 404 | skip negative cache | `redis_errors_total{op="neg_get"}` |
| 4 Postgres lookup by code | not found/inactive → 404 (+`SET neg:{code} 1 EX 60`); expired → 410 | n/a | |
| 5 `SET link:{code}` | `EX min(LINK_CACHE_TTL_SECONDS=300, seconds_to_expiry)` | ignore failure | `redis_errors_total{op="cache_set"}` |
| 6 `XADD clicks` (best effort, same 100 ms bound) | enqueued | **still redirect**, `click_events_dropped_total++` | `redis_errors_total{op="enqueue"}` |
| 7 | `302 Location: <url>` | | |
- Postgres down on a cache **miss** → `503`. Postgres down on a cache **hit** → redirect still served from cache (accepted consistency trade-off: a link edited/deleted during a DB outage may still redirect to its cached value until TTL).
- Status precedence: `is_active=false` → disabled → 404 (even if also expired). Else `expires_at <= now` → 410. Else active.
- `redirect_cache_total{result="hit|miss|negative"}` counts every request that reaches step 2/3/4.
- `HEAD /{code}` (decision D12): an explicit route (FastAPI does not add HEAD to GET routes automatically; verify with a test). Same status and `Location` as GET, no body, **never enqueues a click**.

### 2.1.1 Cache consistency (honest version)
Cache-aside with delete-on-write is **not** race-free: request A can read the old row, writer B updates + deletes the key, then A writes the OLD value back. We accept and **bound** this:
- On link PATCH/DELETE/create: `DEL link:{code}` and `DEL neg:{code}` after the DB commit.
- Worst case staleness = `LINK_CACHE_TTL_SECONDS` (default **300 s**, never longer than link expiry). Not 3600.
- Normal case: edits/deletes are visible on the very next redirect.
- Tests: (a) sequential edit→redirect reflects immediately; (b) a documented test that simulates the race and asserts the stale entry expires within TTL. Known limitation: no singleflight / stampede protection (add small TTL jitter ±10%; singleflight is a documented future improvement).

### 2.2 Click event (stream message fields)
`event_id` (uuid4 string), `link_id`, `code`, `ts` (ISO UTC), `ip_hash` (HMAC-SHA256 hex, `IP_HASH_SECRET`), `user_agent` (truncated 512), `referrer_host` (host only or empty). Client IP = see §6.3 (trusted proxies).

### 2.3 Worker loop (batch isolation + poison handling)
```
ensure group: XGROUP CREATE clicks click-workers 0 MKSTREAM   (ignore BUSYGROUP)
              # '0' not '$': events XADDed before the group existed must not be lost.
              # Re-run this on any NOGROUP error inside the loop (Redis wiped / key deleted).
loop:
  msgs = XREADGROUP GROUP click-workers <consumer> COUNT 100 BLOCK 2000 STREAMS clicks >
  every 30s: XAUTOCLAIM clicks click-workers <consumer> 60000 0-0 COUNT 100   # idle pending (crashed consumers)
  parse UA -> device_type, browser, os, is_bot ; malformed message (missing fields) -> dead-letter path (below)
  TRY batch:
    BEGIN
      valid = SELECT id FROM links WHERE id = ANY(:link_ids)          # pre-filter orphans (deleted links)
      orphans = messages whose link_id not in valid                    # nothing to write for these
      INSERT INTO click_events (...) VALUES ... ON CONFLICT (event_id) DO NOTHING RETURNING link_id, clicked_at, is_bot
      rollups from RETURNING rows where is_bot=false, grouped by (link_id, day)   # only newly inserted rows
      INSERT INTO link_daily_stats ... ON CONFLICT (link_id, day) DO UPDATE SET clicks = link_daily_stats.clicks + EXCLUDED.clicks
    COMMIT
    XACK all message ids in the batch (valid, duplicates, orphans)      # ack strictly AFTER commit
  EXCEPT (any DB error: rollback, e.g. link deleted between SELECT and INSERT -> FK violation):
    FALLBACK: process each message in its own transaction
      success            -> XACK
      FK violation       -> orphan, XACK
      other exception    -> leave UNACKED (it will be redelivered; delivery count rises)
  poison: for pending messages with delivery count >= MAX_DELIVERIES (default 5). Read counts with the extended form
          XPENDING clicks click-workers IDLE <WORKER_CLAIM_IDLE_MS> - + <COUNT>   (XAUTOCLAIM does NOT return delivery counts):
      XADD clicks:dead {original fields + original_id + error + failed_at}
      only if that XADD succeeded -> XACK the original; if the dead-letter XADD fails -> do NOT ack (retry next loop)
SIGTERM: stop reading, finish current batch, ack, exit 0
replay (v1 decision D18: NO replay command): inspect with `XRANGE clicks:dead - +` and re-queue by hand per the runbook (P11.4). A command can be added later.
consumer identity: consumer name = pod name (stable per pod). NEVER run XGROUP DELCONSUMER on a consumer that still has pending entries (it discards them).
  Cleanup order: XAUTOCLAIM its pending entries to a live consumer -> confirm XPENDING for it is 0 -> only then DELCONSUMER.
crash window (REQUIRED TEST): DB commit succeeded but XACK was never sent -> message is redelivered -> INSERT hits ON CONFLICT DO NOTHING -> RETURNING yields no row
  -> the rollup is NOT incremented. Test by calling the batch-processing function, skipping the ack step, then processing the same messages again.
```
Semantics: at-least-once delivery, idempotent processing via `event_id` unique constraint. Bots stored with `is_bot=true`, excluded from rollups and charts.

### 2.3.1 Stream durability — what we do and don't guarantee
- `clicks` is capped with `XADD ... MAXLEN ~ CLICK_STREAM_MAXLEN` (default 100000, ≈ tens of MB) purely as a **memory guard**. Trimming ignores consumer-group state: if the worker is down long enough for the backlog to exceed the cap, the OLDEST entries (possibly still pending) are lost. This is a **bounded-loss design choice**, not a bug.
- Defences: alert on `click_stream_lag` (> 5000 for 5 min) and `click_stream_length` (> 80000) long before the cap; runbook = restart the worker first. Dropped/trimmed events are visible as the gap between `click_events_enqueued_total` and `click_events_processed_total`.
- `appendfsync everysec`: a Redis crash can lose ≈ the last 1 second of writes. We state "at-least-once from the app's view, best-effort across a Redis crash", never "zero loss".
- Product statement (PRD F4): click counts are a **lower bound** during Redis outages.

### 2.4 Auth & session
- Register: strip + lowercase email (≤ 254 chars, validated), argon2id hash, insert (409 on duplicate).
- Login: rate limit (**fail closed**) → verify → `sid = secrets.token_urlsafe(32)` → `SET sess:{sid} {user_id, created_at, last_seen, ua} EX 604800`, `SADD user_sessions:{user_id} sid` (+`EXPIRE` 7d) → cookies. Any pre-existing session cookie's session is destroyed (rotation).
- Authenticated request: read cookie → `GET sess:{sid}` → refresh TTL (at most once/min) → load user. Missing/malformed/expired/unknown session → 401 and clear both cookies.
- CSRF for POST/PUT/PATCH/DELETE with cookie auth: (1) header `X-CSRF-Token` must equal `lf_csrf` cookie (constant-time compare) AND (2) `Origin` header, if present, must be in `ALLOWED_ORIGINS` (if absent, require `Sec-Fetch-Site` ∈ {same-origin, none}; else 403). API-key requests skip CSRF.
- Logout: `DEL sess:{sid}`, `SREM user_sessions:{user_id} sid`. (v1 decision D18: no session list/revoke endpoints or UI.) Change-password: for every OTHER sid in `user_sessions:{user_id}` do `DEL sess:{sid}`; stale sids (key already gone) are removed lazily at that point.
- Change password: verify old, set new hash, revoke all OTHER sessions. **API keys are NOT revoked** by a password change (explicit decision; they have their own revoke).
- API key: `lf_<prefix8>_<secret>`; store unique `prefix` and `sha256(full key)`; constant-time compare; `last_used_at` debounced (≤ 1 write/min/key).
- Sessions vs JWT: instant revocation, session list, tiny cookie. Cost: Redis dependency (sessions lost if Redis loses data → re-login).

## 3. Data model (Postgres 16)
```
users(id uuid pk default gen_random_uuid(), email text not null, password_hash text not null,
      is_active bool not null default true, created_at timestamptz not null default now())
  unique index on lower(email)

links(id uuid pk, user_id uuid not null fk users on delete cascade, code text not null unique,
      destination_url text not null, title text null, is_active bool not null default true,
      expires_at timestamptz null, created_at timestamptz default now(), updated_at timestamptz default now())
  index (user_id, created_at desc)

click_events(id bigint generated always as identity pk, event_id uuid not null unique,
      link_id uuid not null fk links on delete cascade, clicked_at timestamptz not null,
      ip_hash text not null, user_agent text, referrer_host text, device_type text, browser text, os text,
      is_bot bool not null default false)
  index (link_id, clicked_at)

link_daily_stats(link_id uuid fk links on delete cascade, day date, clicks int not null default 0,
      primary key (link_id, day))                                   -- day is a UTC calendar date

api_keys(id uuid pk, user_id uuid fk users on delete cascade, name text not null,
      prefix text not null unique, key_hash text not null, created_at timestamptz default now(),
      last_used_at timestamptz null, revoked_at timestamptz null)
```
`total_clicks` on a link = SUM(`link_daily_stats.clicks`) for that link (bots excluded). The list endpoint computes it with ONE grouped query over the page's link ids (`WHERE link_id = ANY(:ids) GROUP BY link_id`), never one query per link.

Design choices: hard delete of links cascades to events/rollups; events still queued in Redis for a deleted link are **dropped as orphans** (decision, §2.3). Analytics: time series from `link_daily_stats`; breakdowns from `click_events` (`is_bot=false`).

### 3.1 Dates and ranges
- API returns daily bucket labels as plain strings `"YYYY-MM-DD"` (UTC date). The frontend renders them as-is — it must NOT parse them with `new Date()` and convert to local time.
- Timestamps (`created_at`, `clicked_at`, `last_seen`, `expires_at`) are ISO-8601 UTC; frontend may render these in local time.
- `range=Nd` (N ∈ 7, 30, 90) = the last N UTC calendar days **including today** (today is partial): start = today−(N−1), inclusive; end = today, inclusive. Series is zero-filled for every day in the window.

### 3.2 Migration policy
- Alembic, expand → migrate → contract. A migration must be safe with the PREVIOUS application version (old pods serve during rollout).
- Allowed in one release: add table/column (nullable or with default), add index (`CONCURRENTLY` for big tables), add constraint `NOT VALID` then validate later. Forbidden in one release: drop/rename a column the old code uses, change a type, long-running backfills inside the migration.
- Production policy: **no `downgrade`**; fix forward with a new migration. Downgrades are for local dev only.
- Postgres DDL is transactional, so a failed migration normally leaves no partial state; fix by committing a corrected migration. Argo stops at the migration hook, old pods keep serving.
- Migration Job runs on every sync; Alembic is idempotent (`upgrade head`).

## 4. HTTP API (`/api/v1`, JSON)
| Method & path | Auth | Notes |
|---|---|---|
| POST `/auth/register` | none | 201; 409 duplicate; 422 weak/invalid |
| POST `/auth/login` | none | sets cookies; 401 generic; 429 limited |
| POST `/auth/logout` | session | 204 |
| GET `/auth/me` | session | current user |
| POST `/auth/change-password` | session | `{old_password,new_password}` |
| POST `/links` | session/key | `{destination_url, alias?, title?, expires_at?}` → 201 link |
| GET `/links` | session/key | `q, status, page (≥1), page_size (1–100, default 20)` → `{items,total,page,page_size}`; invalid values → 422 |
| GET `/links/{id}` | session/key | 404 if not owner |
| PATCH `/links/{id}` | session/key | omitted field = unchanged; explicit `null` clears `title` / `expires_at`; `destination_url` and `is_active` cannot be null |
| DELETE `/links/{id}` | session/key | 204, hard delete + cascade |
| GET `/links/{id}/analytics?range=7d\|30d\|90d` | session/key | `{total, series[{day,clicks}], referrers[{host,clicks}], devices[], browsers[]}` |
| GET `/analytics/overview?range=30d` | session/key | `{total_links,total_clicks,series[],top_links[]}` |
| POST `/api-keys` | session | plaintext returned once |
| GET `/api-keys` | session | prefix, name, created, last_used |
| DELETE `/api-keys/{id}` | session | revoke |
Root (not `/api/v1`): `GET` and `HEAD /{code}` (redirect), `GET /healthz`, `GET /readyz`. Metrics: separate port (§1.2). Docs: `/api/docs`, `/api/openapi.json` only when `ENABLE_DOCS=true`.
Error body: `{"error":{"code","message","details"}}`. Link object: `id, code, short_url, destination_url, title, is_active, expires_at, created_at, updated_at, status ("active"|"disabled"|"expired"), total_clicks`. `status` = disabled if `is_active=false`, else expired if `expires_at <= now`, else active. List filter `status` uses the same definition.
Versioning: `/api/v1` is additive-only (new optional fields/endpoints OK); breaking changes require `/api/v2`. The generated OpenAPI document is the machine-readable contract.
Aliases: stored exactly as typed (case-sensitive); reserved-word check is case-insensitive. **Reserved:** `api healthz readyz metrics docs openapi.json login register dashboard assets static admin`.

## 5. Redis keys
| Key | Type | TTL | Purpose |
|---|---|---|---|
| `link:{code}` | string (JSON) | ≤ 300s (jittered ±10%) and ≤ link expiry | redirect cache |
| `neg:{code}` | string | 60s | negative cache |
| `sess:{sid}` | string (JSON) | 7d sliding | session |
| `user_sessions:{user_id}` | set | 7d (refreshed on login) | session index (lazy cleanup) |
| `rl:{scope}:{id}:{window}` | string counter | window length | rate limits (Lua) |
| `clicks` | stream | MAXLEN ~100000 | click events; group `click-workers` |
| `clicks:dead` | stream | MAXLEN ~10000 | poison messages |
Rate-limit scopes: `redirect` per client IP (fail open); `login` per IP+email, 5 / 15 min (fail closed); `create` per **user** (shared by session and all that user's API keys), 30/min (**fail closed** if Redis errors: low-volume write path, redirects unaffected; decision D13). All limits come from env (§9) so load tests can raise them.

### 5.1 Redis operations policy
- Config: `appendonly yes`, `appendfsync everysec`, `maxmemory 256mb`, `maxmemory-policy noeviction`, `auto-aof-rewrite-percentage 100`, `auto-aof-rewrite-min-size 64mb`, PVC 1 Gi.
- Memory budget (D11): the container memory limit must exceed `maxmemory` with headroom for the AOF-rewrite fork (copy-on-write) and Redis overhead. Start at limit 512Mi with `maxmemory 256mb`, then MEASURE peak RSS during a forced `BGREWRITEAOF` under write load (P8b) and adjust. Alert on Redis used memory > 80% of `maxmemory` AND on container working set near its limit.
- At maxmemory: write commands fail with OOM → handled as a Redis error: redirects fail open; logins fail closed; new sessions impossible; events dropped (counted). Alert on Redis memory > 80%.
- Recovery: restart the pod (AOF replays from the PVC). Corrupt AOF → run `redis-check-aof --fix` from a debug pod. Last resort: wipe the PVC — consequences: sessions lost (re-login), rate limits reset, cache rebuilds, queued events lost (visible in the enqueued-vs-processed gap).

## 6. Security details
### 6.1 Cookies
| | local dev/compose | production |
|---|---|---|
| `lf_session` | HttpOnly, SameSite=Lax, Path=/, no Domain (host-only), Secure=false | HttpOnly, **Secure**, SameSite=Lax, Path=/, **no Domain** (host-only on `app.DOMAIN`, so it is never sent to `go.DOMAIN`) |
| `lf_csrf` | not HttpOnly, SameSite=Lax, Path=/, Secure=false | not HttpOnly, **Secure**, SameSite=Lax, Path=/, host-only |
`COOKIE_SECURE` env controls the Secure flag (true in prod).
### 6.2 Passwords
argon2-cffi `PasswordHasher` with RFC 9106 low-memory profile (t=3, m=64 MiB, p=4 — use the library defaults and assert them in a test). Length 10–128 characters. Hash/verify run in a threadpool behind an `asyncio.Semaphore(2)` so concurrent logins can't exhaust pod memory (2 × 64 MiB + app ≈ 256–300 MiB, so the API pod limit is 384Mi; decision D10).
### 6.3 Client IP and trusted proxies
Never trust `X-Forwarded-For` blindly. `TRUSTED_PROXY_COUNT` = number of reverse proxies in front of the API (local: 0; kind/Traefik: 1; EKS/ALB: 1, or 2 if another proxy sits behind it). Client IP = the entry `TRUSTED_PROXY_COUNT` positions from the right of `X-Forwarded-For`; with count 0 use the socket peer address. Used for rate limiting and `ip_hash`. Test with a spoofed header.
### 6.4 Other
Database connections to RDS use TLS **with certificate verification** (`verify-full`, RDS CA bundle baked into the image; `DATABASE_SSL_MODE` / `DATABASE_SSL_CA_FILE`; decision D14). Parameterized queries only (ORM); URL validation (§ PRD F2); no open redirect to our own short domain; no secrets in logs/images/repo; HTTP security headers on the SPA (CSP default-src 'self', X-Content-Type-Options, frame-ancestors none, Referrer-Policy).

## 7. Metrics (Prometheus)
- `http_request_duration_seconds{method,route,status}` histogram, buckets `[0.001,0.0025,0.005,0.01,0.025,0.05,0.1,0.25,0.5,1]`. `route` = route template (redirect is `/{code}`), never the raw path.
- `http_requests_total{method,route,status}`
- `redirect_cache_total{result="hit|miss|negative"}`
- `redis_errors_total{op="ratelimit|cache_get|neg_get|cache_set|enqueue|session|other"}`
- `click_events_enqueued_total`, `click_events_dropped_total`
- `click_events_processed_total{status="ok|duplicate|orphan|dead"}` (worker)
- `click_stream_lag` (gauge: group's undelivered entries from `XINFO GROUPS` `lag`; if Redis reports it as nil after trims/deletes, fall back to `XLEN` as an upper bound and document it), `click_stream_pending` (gauge: group's pending count), `click_stream_length` (gauge: `XLEN`)
- `click_worker_batch_seconds` histogram
- `rate_limit_rejections_total{scope="redirect|login|create"}`
- DB pool gauges (optional).

### 7.1 SLI / SLO / alert (distinct on purpose)
| | Definition | Value |
|---|---|---|
| SLI | server-side redirect latency from `http_request_duration_seconds{route="/{code}"}` | measured |
| Benchmark target (SLO-ish) | warm-cache redirect p95, server-side | **< 50 ms** (report the actual number) |
| Alert threshold | same SLI, 5 min | p95 **> 250 ms** (5× worse than target = something is broken) |
| k6 client-side threshold | includes network from where k6 runs | p95 < 250 ms from inside the cluster; laptop numbers include your internet and must be labelled as such |
Other alerts: 5xx ratio > 2% for 5 min **and** request rate > 0.1/s; `click_stream_lag` > 5000 for 5 min; `click_stream_pending` > 500 for 10 min; `click_stream_length` > 80000; `click_events_dropped_total` rate > 0 for 5 min; Redis memory > 80%; crash-looping pods; API target down.

## 8. Failure behaviour (demo this; say it in interviews)
| What dies | Redirects | Logins/API | Analytics |
|---|---|---|---|
| Redis | work (Postgres fallback, slower), limiter fails open | logins fail closed; existing sessions invalid (can't read `sess:`) | new clicks dropped and counted; AOF restores entries on restart (≤ ~1 s of writes may be lost) |
| Worker | work | work | lag/pending grow; drain after restart; loss only if backlog exceeds the stream cap |
| Postgres | cache hits still redirect; misses → 503 | fail | events accumulate in the stream; processed when DB returns (cap applies) |
| One API pod | Service routes around; PDB keeps ≥ 1 | same | same |
| Bad deploy | old ReplicaSet keeps serving if new pods never become Ready | | rollback via `git revert` in the gitops repo |
| Failed migration | old pods keep serving; Argo sync halts at the migration step | | |

## 9. Capacity budgets
### 9.1 Environment variables (defaults)
`DB_POOL_SIZE=3, DB_MAX_OVERFLOW=2` (API, = 5 per pod), worker pool 2+1, `REDIS_SOCKET_TIMEOUT_MS=100, REDIS_CONNECT_TIMEOUT_MS=200, LINK_CACHE_TTL_SECONDS=300, CLICK_STREAM_MAXLEN=100000, TRUSTED_PROXY_COUNT=0, RATE_LIMIT_REDIRECT_PER_MIN=120, RATE_LIMIT_CREATE_PER_MIN=30, RATE_LIMIT_LOGIN_MAX=5, METRICS_PORT=9101, PUBLIC_REDIRECT_HOST (empty locally), ALLOWED_ORIGINS, SESSION_TTL_SECONDS=604800, COOKIE_SECURE, ENABLE_DOCS, ENV, LOG_LEVEL, DATABASE_URL, REDIS_URL, SHORT_URL_BASE, IP_HASH_SECRET, WORKER_BATCH_SIZE=100, WORKER_BLOCK_MS=2000, WORKER_CLAIM_IDLE_MS=60000, WORKER_CLAIM_INTERVAL_SECONDS=30, MAX_DELIVERIES=5, CLICK_DEAD_MAXLEN=10000, DATABASE_SSL_MODE (disable locally, verify-full on RDS), DATABASE_SSL_CA_FILE`. Tests lower the `WORKER_*` values so reclaim/poison tests don't wait 60 s.
### 9.2 Postgres connection budget
`C_total = N_api × (pool+overflow) + N_worker × (pool+overflow) + C_migration + C_admin`
With HPA max 6 API pods: 6×5 + 1×3 + 1 + ~10 headroom ≈ 44. **Check the real limit** on the actual instance (`SHOW max_connections;`) — for a db.t4g.micro it is roughly in the 80–110 range (derived from instance memory; verify, don't assume). Keep `C_total` ≤ ~60% of it. If you raise HPA max or pool size, redo this arithmetic.
### 9.3 Cluster resource budget (ESTIMATES — validate with `kubectl top` on kind before trusting)
| Workload | Replicas | Request (cpu / mem) each | Note |
|---|---|---|---|
| api | 2 (HPA→6) | 100m / 256Mi (limit 384Mi) | covers Semaphore(2) argon2 (2×64 MiB) + app |
| worker | 1 | 100m / 128Mi | |
| frontend (nginx) | 2 | 25m / 32Mi | |
| redis | 1 | 100m / 256Mi (limit 512Mi) | `maxmemory 256mb` + AOF-rewrite headroom; measure (§5.1) |
| postgres (kind only) | 1 | 100m / 256Mi | RDS in cloud |
| Argo CD (all components) | — | ≈ 500m / 1 Gi total | rough |
| kube-prometheus-stack (prom, grafana, alertmgr, operator, exporters) | — | ≈ 700m / 1.5 Gi total | rough; set retention short (e.g. 2d) |
| AWS LB Controller, metrics-server, coredns, kube-proxy, CNI, EBS CSI | — | ≈ 400m / 600Mi total | rough |
**Also check max pods per node.** Pod count on EKS is limited by ENI/IP capacity, not just CPU/memory; with the default VPC CNI a t3.medium allows only a small number of pods (about 17, VERIFY the real `maxPods` for your node type and CNI mode). Argo CD + monitoring + kube-system + the app can exhaust pod slots before CPU. Options: VPC CNI prefix delegation, 3 nodes, or larger nodes. Don't hard-code 17; check on the actual cluster.
A t3.medium is 2 vCPU / 4 GiB with roughly 1.9 vCPU / ~3.3 GiB allocatable. Two nodes ≈ 3.8 vCPU / ~6.6 GiB. The above totals ≈ 2.2 vCPU / ~4.0 GiB at 2 API pods — it fits with limited headroom, and **HPA scaling to 6 API pods may leave pods Pending**. Plan: validate on kind; on EKS watch for Pending; if needed use 3 nodes or t3.large (cost goes up — note it).
### 9.4 What the CPU-based HPA demonstrates (and not)
It shows reactive scale-out when API CPU is the bottleneck. It does NOT react to Redis latency, Postgres connection saturation or worker backlog. Say so in the README; name KEDA/queue-based scaling as the next step for the worker.

## 10. Backup & restore
- **kind:** `scripts/pg_dump.sh` / `pg_restore` documented. One restore drill (P8b): dump from kind → restore into a fresh Compose Postgres → run the app against it → verify link and click counts match.
- **EKS/RDS:** automated backups on with `backup_retention_period = 1`; for this DISPOSABLE demo environment `skip_final_snapshot = true` and `deletion_protection = false` — a conscious, documented decision (production: final snapshot + deletion protection + longer retention + Multi-AZ). Take a manual snapshot before destroy if you want to keep data.
- Redis: see §5.1. A backup nobody has restored is an assumption; the P8b drill is the evidence.

## 11. Deployment ordering (Argo CD, enforced — not "remember to sync")
Root "app of apps" (`argocd/root.yaml`) creates child Applications with `argocd.argoproj.io/sync-wave`:
- wave **-3**: `sealed-secrets` (controller + CRD)
- wave **-2**: `monitoring` (kube-prometheus-stack: provides the ServiceMonitor/PrometheusRule CRDs the app chart uses)
- wave **0**: `linkforge`
Inside the `linkforge` app (resource-level sync waves):
- wave **-2**: `SealedSecret`s (+ ConfigMap)
- wave **-1**: Postgres (kind only) and Redis StatefulSets + Services (Argo waits for them to be Healthy)
- wave **0**: migration Job (Argo `Sync` hook, `hook-delete-policy: BeforeHookCreation`)
- wave **1**: API, worker, frontend Deployments, Services, Ingress, HPA, PDB, ServiceMonitors
Why not PreSync: PreSync runs before everything in the Sync phase — on a fresh kind cluster Postgres wouldn't exist yet and the secret may not either. Sync hooks respect waves.
Caveats to VERIFY in P7D (do not assume): (a) Argo's built-in health check for `Application` resources in app-of-apps (if waves don't wait, add the documented Lua health check to `argocd-cm`); (b) `SealedSecret` has no built-in health check, so the migration pod may briefly wait for the unsealed Secret (kubelet retries — acceptable).

## 12. Design decisions (and the interview answer for each)
- **302 not 301:** 301 is cached by browsers permanently → lost analytics, edits don't apply.
- **Random base62 + unique constraint + retry:** 62^7 ≈ 3.5e12; collisions rare and handled; sequential IDs are guessable and leak volume.
- **Cache-aside, delete-on-write, bounded staleness:** simple; race acknowledged and bounded by a 300 s TTL (§2.1.1).
- **Negative cache 60 s:** protects Postgres from scanners.
- **Redis Streams over Celery/Kafka:** one durable queue with consumer groups, acks, pending lists, reclaim is all we need.
- **At-least-once + idempotent writes** rather than "exactly once".
- **Rollup table + raw events:** cheap time series, rich breakdowns, retention possible on raw.
- **Fail open on redirect limiter, closed on login limiter.**
- **Stream cap = memory guard with bounded loss** (§2.3.1) — alerted, not hidden.
- **Sessions vs JWT:** §2.4.
- **Redis in-cluster, not ElastiCache:** cost; we own persistence (single replica, AOF). Production: ElastiCache.
- **Metrics on a separate port:** the public ingress physically cannot route to `/metrics`.
- **GitOps with a separate repo:** CI tag commits don't retrigger CI; audit trail; rollback = `git revert`.
