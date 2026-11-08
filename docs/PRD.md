# LinkForge — Product Requirements Document (rev 2)

Status: v1 locked. Priority tags: **[M]** must, **[S]** should, **[C]** could (only if all [M]/[S] are done). If this file is silent on a behaviour, ask — don't invent.

## 1. What it is
LinkForge is a URL shortener with click analytics. A registered user creates short links, shares them, and sees how many people clicked, when, from where (referrer host), and on what device/browser. Anyone with a short link is redirected to the destination.

## 2. Why it exists (honest version)
Portfolio project for backend/SDE and DevOps roles. The product is deliberately small. The value is in the engineering: a fast cached redirect path, an asynchronous click pipeline that never slows the redirect, real auth and sessions, tests against real infrastructure, and a full CI/CD + Kubernetes + GitOps + observability story with measured numbers.

## 3. Users
- **Visitor (anonymous):** follows short links. Needs speed and a correct redirect.
- **Owner (registered user):** manages links and reads analytics in the web dashboard.
- **Developer (API-key holder):** an owner who creates links programmatically.

## 4. Features and acceptance criteria

### F1 Registration & sessions [M]
- Register with email + password. **Email:** trimmed, lowercased, ≤ 254 chars, syntactically valid, unique. **Password:** 10–128 chars. Stored as argon2id.
- Login creates a Redis-backed server session (7-day sliding). Cookie `lf_session` is HttpOnly, SameSite=Lax, Secure in prod; host-only (not shared with the short-link host). Session ID rotates on login. Logout destroys the session server-side.
- Settings page has API keys and change password. Change password revokes all OTHER sessions; **it does not revoke API keys**. (v1 decision D18: no session list/revoke UI.)
- Login rate limit: 5 failed attempts / 15 min per IP+email → 429 (fails closed if Redis is unavailable).
- AC: wrong password and unknown email return the same 401; a revoked/expired/malformed session cookie gets 401 and cookies are cleared; unsafe cookie-authenticated requests without a valid CSRF header **or** with a non-allowed `Origin` get 403.
- Out of scope: password reset, email verification, OAuth, roles.

### F2 Create & manage links [M]
- Create: `destination_url` (required), optional alias (3–32 chars `[A-Za-z0-9_-]`, case-sensitive, stored as typed; reserved-word check case-insensitive), optional title, optional `expires_at` (must be in the future).
- Without alias → random 7-char base62; collisions retried transparently.
- Link `status`: `disabled` if inactive; else `expired` if `expires_at <= now`; else `active`.
- List: search (code/title/URL), filter `status`, pagination `page ≥ 1`, `page_size` 1–100 (default 20), newest first; invalid values → 422.
- Edit (PATCH): omitted field = unchanged; explicit `null` clears `title` and `expires_at`; `destination_url` and `is_active` cannot be null. Delete: permanent, removes click data.
- Response includes `short_url` built from server-side `SHORT_URL_BASE`, and `total_clicks` (bots excluded, from daily rollups; one grouped query per page, no N+1).
- AC: duplicate alias → 409; reserved alias → 422; destination not http/https, > 2048 chars, or pointing to our own short domain → 422; user A cannot read/modify/delete user B's link (404); edits/deletes are visible on the next redirect in the normal case, and in the worst concurrent-read race the stale value expires within the cache TTL (≤ 5 min).

### F3 Redirect [M]
- `GET /{code}` → **302**. Disabled or unknown → 404. Expired → 410. Postgres unavailable on a cache miss → 503. `HEAD /{code}` returns the same status and `Location` with no body and is **never counted as a click**.
- On the public short-link host ONLY this route is served.
- AC: measured p95 redirect latency (server-side, warm cache) is reported against the **target** of < 50 ms (a target, not a result — publish actual numbers). A Redis outage must not break redirects (Postgres fallback, still 302). Redirect rate limit (default 120/min per client IP, configurable) → 429, and it fails open.

### F4 Click analytics [M]
- Every successful redirect **attempts** to enqueue a click event asynchronously (Redis Stream). If the queue is unavailable the redirect still succeeds and the dropped event is counted (`click_events_dropped_total`). A worker stores events and maintains daily counts.
- Captured per click: time, hashed IP (HMAC, never raw), user agent, referrer **host only**, device type, browser, OS, is_bot.
- Bot policy: best-effort detection using a maintained UA-parsing library (e.g. `user-agents`; check maintenance at install time, else `ua-parser` + a small documented bot regex). Bots are stored with `is_bot=true` and excluded from counts/charts. Detection is imperfect by nature.
- **Counts are a lower bound** during Redis outages or if the stream cap is exceeded; the dashboard footer says "counts may undercount during outages".
- Link analytics `range` = 7d/30d/90d = last N UTC days including today (today is partial), zero-filled: total clicks, clicks per day (UTC date labels), top referrers, device split, browser split.
- Overview (`range=30d`): total links, total clicks, per-day series, top 5 links.
- AC: replaying the same event never double counts; with the worker stopped, redirects still work and the backlog is processed after restart; analytics appear within ~10 s of a click under normal conditions; one orphaned event in a batch does not block the other events.

### F5 Dashboard UI [M]
- Screens: Landing (polished, explains the product), Register, Login, Dashboard (stat cards + 30-day trend), Links (table + create/edit modal + copy), Link analytics, Settings (API keys, change password).
- AC: every data view has loading, empty, error states; works at 1280px and 390px; protected routes redirect to login; daily labels shown as UTC dates exactly as the API returns them.

### F6 API keys [M]
- Create named key (shown ONCE; stored as sha256 + prefix), list (prefix, name, created, last used), revoke.
- A key authenticates link endpoints and analytics via `X-API-Key`. Create-link limit 30/min **per user** (shared by the user's session and all their keys).
- AC: revoked key → 401 immediately; plaintext never retrievable after creation.

### F7 Operability [M]
- `/healthz` (liveness), `/readyz` (Postgres required; Redis down = degraded but ready), Prometheus metrics on an internal-only port, structured JSON logs with request ID, OpenAPI docs at `/api/docs` in dev only.

### F8 Data retention [C]
- Scheduled job deletes raw `click_events` older than 90 days; daily rollups are kept. (Task is in P11 as optional.)

## 5. Non-goals (v1)
Pricing/billing, teams/roles, password reset, OAuth, email sending, QR codes, geo maps, A/B or multi-destination links, per-user custom domains, link previews, browser extension, mobile app, i18n.
Infra non-goals: Jenkins, Nexus, SonarQube, ELK, Kafka, Istio, microservices, Celery, any LLM/AIOps feature, NetworkPolicies (deferred; mentioned as "production would add").

## 6. Non-functional requirements
- **Degradation behaviour:** the matrix in ARCHITECTURE.md §8, demonstrated by drills.
- **Security:** argon2id, parameterized queries, CSRF (token + Origin check), rate limits with trusted-proxy-aware IPs, no open redirect to our own domain, HttpOnly cookies, hashed IPs and API keys, no secrets in repo/images/logs, non-root containers, dependency + image scanning gated in CI. See `docs/SECURITY.md`.
- **Quality:** ruff clean, tsc strict, tests against real Postgres/Redis in CI. Target ≥ 80% line coverage on backend services (measure it).
- **Performance (k6, honest reporting):** separate scenarios — warm-cache redirects, mixed/cache-miss redirects, link creation. p50/p95/p99, RPS, error rate (429s reported separately from errors), cache hit ratio, HPA scale-out. Label whether k6 ran inside or outside the cluster.
- **UI quality:** this is a portfolio piece. A deliberately designed, consistent interface (design system, dark mode, responsive at 1280/390, tasteful charts, good empty/loading states). Screenshots are part of the README.
- **Cost:** local first. Cloud phase is time-boxed; `terraform destroy` + console check is mandatory.

## 7. Definition of done
1. All [M] features work locally via `docker compose up` and on kind via Argo CD.
2. CI green on main; image scanned, pushed to ECR (immutable SHA tag); gitops repo auto-updated.
3. Deployed once on EKS from a clean `terraform apply`, load tested, drills performed, then destroyed and verified.
4. README with architecture diagram, screenshots (dashboard + Grafana), real k6 numbers, demo video, "what breaks when Redis dies".
