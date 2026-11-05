# LinkForge — project memory for Claude Code

URL shortener + click analytics platform. Placement/portfolio project: must be correct, tested, observable and deployable — NOT feature-rich. Read this file fully every session.

## Source of truth (read in this order; the first two are specs, the rest are guidance)
1. `PRD.md` — WHAT we build (features, acceptance criteria, non-goals)
2. `docs/ARCHITECTURE.md` — HOW it works (flows, schema, API, Redis keys, metrics, failure behaviour, budgets, deployment ordering)
3. `docs/TASKS.md` — phases. ONE phase per session. Append to the Progress Log at the end.
4. `SECURITY.md` — threat model and controls (consult when touching auth, ingress, secrets, IAM)
5. `docs/DEVOPS_GUIDE.md` — written for the HUMAN to learn DevOps. It is a reference, not a spec. Read the section the current task names (e.g. "§9"); the user has explicitly allowed reading it whole when teaching a phase. Where it conflicts with ARCHITECTURE.md, ARCHITECTURE.md wins.
6. `docs/DECISIONS.md` — decisions made in design review (D1–D22). If a decision there conflicts with older text elsewhere, DECISIONS.md wins and the older text should be fixed.

## Stack (locked — do not swap without asking)
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2, redis-py (asyncio), argon2-cffi, prometheus-client
- Click worker: same image as the API, different command (`python -m app.worker`). Redis Streams. NO Celery.
- Frontend: React + TypeScript + Vite + Tailwind + shadcn/ui + Recharts + TanStack Query + React Router
- Data: PostgreSQL 16, Redis 7 (AOF on)
- Infra: Docker, GitHub Actions, Terraform, kind (local K8s), EKS (cloud), Helm, Argo CD, Sealed Secrets, kube-prometheus-stack, k6
- Region ap-south-1. Two repos: `linkforge` (this one: app + Terraform) and `linkforge-gitops` (Helm chart + Argo apps).

## Commands
```
docker compose up -d postgres redis          # local deps
cd backend && pip install -r requirements.txt -r requirements-dev.txt
cd backend && alembic upgrade head
cd backend && uvicorn app.main:app --reload --port 8000
cd backend && python -m app.worker           # click consumer
cd backend && pytest -q                      # needs postgres+redis running
cd backend && ruff check . && ruff format --check .
cd frontend && npm ci && npm run dev         # :5173, proxies /api -> :8000
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
docker compose up --build                    # full prod-like stack, UI on :8080
```

## Invariants (violating any is a bug)
- API prefix `/api/v1`. Redirect is `GET /{code}` at root, registered LAST. **302**, never 301. Unknown/disabled → 404, expired → 410, DB down on cache miss → 503. Never 500 for a bad code.
- Every Redis call on the redirect path has a short timeout (default 100 ms) and its own fallback (ARCHITECTURE §2.1). A Redis error never becomes a 500. `XADD` is best-effort: on failure still redirect and count `click_events_dropped_total`.
- Redirect limiter fails OPEN. Login limiter fails CLOSED.
- On the public short-link host (`PUBLIC_REDIRECT_HOST`) ONLY `GET`/`HEAD /{code}` is served. `HEAD` never counts as a click. `/metrics` lives on a separate internal port (API :9101, worker :9100), never behind an Ingress.
- Worker follows ARCHITECTURE §2.3 exactly: pre-filter orphans, bulk insert `ON CONFLICT (event_id) DO NOTHING RETURNING`, rollups only from inserted rows, `XACK` only AFTER commit, per-event fallback on batch failure, dead-letter ack only after the dead-letter XADD succeeded.
- Cache TTL ≤ 300 s and ≤ link expiry; invalidate by DEL on write. Stale-race is accepted and bounded (§2.1.1) — don't claim it's race-free.
- Passwords argon2id (library defaults, 10–128 chars). Never log passwords, session IDs, API keys, raw IPs. Store `HMAC(IP_HASH_SECRET, ip)` only. Client IP only via the trusted-proxy rule (§6.3), never raw `X-Forwarded-For`.
- Sessions are server-side in Redis; cookie `lf_session` HttpOnly, Secure (prod), SameSite=Lax, host-only. Rotate on login. CSRF = `X-CSRF-Token` double-submit AND `Origin` check on unsafe cookie-auth requests.
- API keys: `X-API-Key`, store sha256 + prefix only, show plaintext once.
- Every query on `links` is scoped by `user_id`; cross-user access returns 404.
- Daily bucket labels are UTC `YYYY-MM-DD` strings; never timezone-convert them.
- Schema changes only via backward-compatible Alembic migrations (expand/contract). No production downgrades.
- Config only via env vars (pydantic-settings). No secrets in git, images, or logs.

## Testing & verification rules
- Integration tests hit REAL Postgres and Redis. Do not mock the DB or Redis.
- Each feature ships with its tests in the same commit. A phase is not done until its "Done when" is demonstrably true.
- At the end of every task, REPORT: which commands you ran, which passed, which failed, and what you did NOT verify. Never say "tests pass" without having run them in this session.

## Working agreements
- **Teaching mode (D1).** Claude owns backend/frontend code and tests. For DevOps phases Claude writes commented files AND explains what/why/how-it-works, gives the exact commands + expected output + a "break it" exercise from DEVOPS_GUIDE; the human runs the commands. End each phase with a 5-line "explain it back" summary. Shell is WSL2; the repo lives outside OneDrive.
- **No invention.** If a spec is silent (endpoint, DB field, env var, metric, infra resource, dependency), STOP and ask or propose a documented decision. Do not guess.
- Plan first: propose a short plan per phase/task and wait for approval before coding.
- Small commits, one per task, conventional messages (`feat:`, `fix:`, `chore:`, `test:`, `docs:`).
- Do not add features not in PRD.md. Do not add dependencies without saying why.
- Explicitly NOT in v1: Jenkins, Nexus, SonarQube, ELK, Kafka, Istio, microservices, Celery, OAuth, password reset, geo maps, roles, pricing page, session list/revoke UI, dead-letter replay command, External Secrets/Secrets Manager.
- Cloud safety: never run `terraform apply/destroy`, mutating `aws` commands, or `kubectl` against a cloud cluster. Never ask for, request, read, or store credentials. Write the code and print the exact command; the human runs it in their own shell. (`.claude/settings.json` denies the dangerous ones; do not try to work around it. Those deny rules are a guardrail, not a boundary: the real control is that cloud credentials never exist in a shell Claude runs in. It also blocks `kubectl apply/delete` on kind by design — the human runs those so they learn them.)
- If two files conflict, stop and ask.

## Layout
```
backend/ frontend/ infra/ docs/ PRD.md SECURITY.md CLAUDE.md docker-compose.yml .github/workflows/ .claude/settings.json
```
Scoped rules: `backend/CLAUDE.md`, `frontend/CLAUDE.md`, `infra/CLAUDE.md`.
