# LinkForge — Phased Task List (rev 2)

## How to use this with Claude Code (read once)
1. Open Claude Code in the repo root. It auto-reads `CLAUDE.md`.
2. **One phase per session.** Start with the phase's prompt. End with: "Update the Progress Log in docs/TASKS.md and report what you ran, what passed, what failed, and what you did NOT verify." Then `/clear` before the next phase.
3. Always **plan first** (plan mode / "propose a plan, wait for my OK"). Read it. Reject plans that add scope or invent things the specs don't say.
4. Review every diff. Run the tests yourself. A green run you did not watch is not evidence.
5. Commit per task. Never let Claude commit secrets. Never paste AWS keys into a chat — use SSO/profile in your own shell.
6. DevOps phases: Claude writes files; **you run the commands** (`.claude/settings.json` blocks Claude from running terraform apply/destroy, aws, kubectl apply/delete, helm install/upgrade). If you want Claude to run `kubectl apply` against your local kind cluster, loosen that rule yourself, knowingly.
7. If something fails, paste the exact error, not a paraphrase.

Legend: ☐ todo · ☑ done. "Done when" is the acceptance test — not "code written".
Phase order: P0 → P0.5 → P1 → **P1S (walking skeleton)** → P2 → P3 → P4 → P5 → P6 → P7A → P7B → P7C → P7D → P8 → P8b → P9 → P10 → P11.
Working mode (decision D1): Claude writes app code + tests; for DevOps phases Claude writes commented files, explains what/why/how it works, gives the exact commands + expected output + a "break it" exercise; the human runs them and explains the phase back before moving on. Decisions are logged in `docs/DECISIONS.md`.

---

## P0 — Setup & guardrails (you, ~half a day)
- ☐ P0.0 Environment (D20): WSL2 and Docker are already installed. Use **WSL2 (Ubuntu)** as the one shell. Move the repo to the WSL filesystem (OUTSIDE OneDrive): `mkdir -p ~/linkforge && cp -r "/mnt/c/Users/Sai Charan/OneDrive/Desktop/P/linkforge/." ~/linkforge/ && cd ~/linkforge && git init -b main && git config core.autocrlf input && printf '* text=auto eol=lf\n' > .gitattributes && git add -A && git commit -m "docs: initial specs and decisions"`; check `docker version && docker compose version && free -h`; start the next Claude Code session from `~/linkforge`; keep the OneDrive copy as a backup until the first push, then delete it. Cap WSL with `%UserProfile%\.wslconfig` (`[wsl2] memory=8GB processors=4`), then `wsl --shutdown`. Close memory-hungry Windows apps during kind/monitoring phases.
- ☐ P0.1 Install (inside WSL where possible): Docker, Python 3.12, Node 20+ LTS, git, `kubectl`, `kind`, `helm`, `terraform` (≥ 1.11), `aws` CLI v2, `k6`, `kubeseal`, `gh`, `trivy`.
- ☐ P0.2 Create GitHub repos `linkforge` and `linkforge-gitops`. Turn on branch protection for `main` later (P1, after the first CI run exists): require status checks, no force-push.
- ☐ P0.3 AWS: root MFA; create your day-to-day access via **IAM Identity Center** (preferred; short-lived SSO sessions) — an IAM user with MFA is the fallback, not the goal. Budget alerts at $5/$20/$50. Open Billing → Free Tier/Credits and write down your credit balance and expiry. Region ap-south-1.
- ☐ P0.4 Copy these docs (incl. `.claude/settings.json`) into the repo, commit.
Done when: every tool prints its version; budget alert exists; repos exist; you know your credit balance and expiry date.
DevOps pointer: §1, §6.

## P0.5 — Design validation (Claude, NO application code)
Goal: catch contradictions before any code exists.
- ☐ P0.5.1 Read PRD.md, ARCHITECTURE.md, TASKS.md, SECURITY.md, the CLAUDE.md files. List every contradiction, ambiguity, or missing decision. Do not fix silently — list them.
- ☐ P0.5.2 Propose the final API contract as an OpenAPI skeleton (paths, request/response schemas, error shape) and confirm the data model + indexes against ARCHITECTURE §3–4.
- ☐ P0.5.3 Write `docs/TRACEABILITY.md`: table PRD requirement → task id → test name(s).
Done when: you've read the list, answered every open question (decisions recorded in `docs/DECISIONS.md`), and approved the contract.
Prompt:
> Read CLAUDE.md, PRD.md, docs/ARCHITECTURE.md, SECURITY.md, docs/TASKS.md. Do P0.5 only: list contradictions/ambiguities/missing decisions, propose an OpenAPI skeleton, and produce docs/TRACEABILITY.md. Do not write application code. Wait for my answers.

## P1 — Backend core + CI basics
Goal: link CRUD + redirect on Postgres, tests, CI green from day 1.
- ☑ P1.1 Project skeleton, config (`pydantic-settings`), async DB, `/healthz` + `/readyz`. *Test:* app boots; readyz fails when DB unreachable.
- ☑ P1.2 Models + Alembic initial migration (all tables, constraints, indexes per ARCH §3). *Test:* upgrade on empty DB works; constraints enforced (unique email, event_id, FK cascade).
- ☑ P1.3 `core/urls.py` validation + `core/shortcode.py`. *Test:* table-driven valid/invalid URLs, reserved/alias rules, collision retry.
- ☑ P1.4 Link CRUD with a TEMPORARY dev user (removed in P2), PATCH null/omitted semantics, status field, pagination limits. *Test:* each rule from PRD F2.
- ☑ P1.5 Redirect on Postgres only (302/404/410, precedence). *Test:* each status.
- ☑ P1.6 Compose with postgres + redis; pytest fixtures on a separate test DB.
- ☑ P1.7 GitHub Actions `ci.yml`: backend job (service containers) + ruff + pytest; add dependency audit (`pip-audit`) and secret scan (`gitleaks`) as non-blocking first.
Done when: pytest green locally and in Actions; `curl -i localhost:8000/<code>` returns 302; migration works on an empty DB.
Prompt:
> P1 only. Plan P1.1–P1.7 (one commit each, tests named). Wait for approval.
DevOps pointer: §2, §3.

## P1S — Walking skeleton (DevOps early; decision D4)
Goal: a complete CI → GitOps → kind loop for a MINIMAL app (links + redirect on Postgres, no Redis/auth/frontend yet), so infra problems surface before features pile up. Everything here is later extended, not thrown away (the raw manifests are the only throwaway).
- ☑ P1S.1 Backend Dockerfile (multi-stage, non-root, healthcheck) + `.dockerignore`; Compose with postgres, one-shot migrate, api. *Verify:* `docker compose up --build` → `curl -i localhost:8000/<code>` = 302; `id` is non-root; note image size.
- ☑ P1S.2 Placeholder frontend: a static nginx-unprivileged page (the real SPA comes in P4/P5) so the two-host ingress can be exercised.
- ☑ P1S.3 (written + rehearsed locally; first real run on main still to be watched) CI: extend `ci.yml` to build the image; new `build-push.yml` on main → build → Trivy → push to **public GHCR** with `GITHUB_TOKEN` (`:<short-sha>`, no AWS yet) → commit the new tag to `linkforge-gitops` (token scoped to that one repo).
- ☑ P1S.4 (a, b, c all run by the human on kind; drills recorded in docs/RESULTS.md) kind cluster + Traefik + metrics-server; raw manifests first (learn the objects), then the minimal Helm chart in `linkforge-gitops` with `envs/kind/values.yaml` (postgres StatefulSet, migration Job, api, placeholder frontend, Ingress for `app.localtest.me` and `go.localtest.me`).
- ☑ P1S.5 (run by the human 2026-11-06: Argo install, sync-wave deploy and all three proofs observed, see docs/RESULTS.md) Argo CD (pinned) + one `linkforge` Application (automated sync, prune, selfHeal). Secrets by hand for now.
Done when: merge to main → CI builds/scans/pushes to GHCR → gitops tag bumps → Argo rolls it out on kind with no manual step; `go.localtest.me/<code>` redirects; `kubectl delete pod` heals; a bad tag leaves old pods serving and `git revert` fixes it.
Prompt:
> P1S only. Plan P1S.1–P1S.5. For each DevOps step explain what/why/how it works, give the exact commands and expected output, and a "break it" exercise. I run the commands. Wait for approval.
DevOps pointer: §2, §3, §7, §8, §9.

## P2 — Redis cache, rate limits, auth sessions, API keys
- ☑ P2.1 Redis client with timeouts (100/200 ms) + `redis_errors_total`. *Test:* unreachable Redis → helper returns fallback, no exception. (Also: hung Redis, deadline, `/readyz` redis field, D56.)
- ☑ P2.2 (D57) Redirect cache-aside + negative cache + DEL invalidation + TTL rule (ARCH §2.1). *Test:* hit/miss/negative; edit→redirect immediate; stale-race entry expires within TTL; Redis down → still 302.
- ☑ P2.3 (D58; login/create limiters tested at the limiter level, wired in P2.4/P2.7) Lua rate limiter, env-driven limits, `client_ip` with trusted-proxy rule. *Test:* limit hit → 429; Redis down → redirect allowed (open), login denied (closed); spoofed XFF ignored.
- ☑ P2.4 (D59; CSRF/Origin checks are P2.5, links still use the dev user until P2.6) Register/login/logout/me, argon2 (semaphore), Redis sessions, rotation, cookie flags, malformed-session handling. *Test:* ARCH §2.4 cases.
- ☑ P2.5 (D60) CSRF token + Origin check; change password (revokes other sessions only; no session list/revoke endpoints, D18). *Test:* missing token 403, bad Origin 403, change-password kills other sessions but not the current one or API keys.
- ☑ P2.6 (D61) Replace dev user with real auth; scope every `links` query by `user_id`. *Test:* cross-user = 404 everywhere.
- ☑ P2.7 (D62) API keys (create/list/revoke, `X-API-Key`), per-user create limiter. *Test:* revoked → 401; plaintext never re-readable; limit shared across sessions + keys.
- ☑ P2.8 (D63; HEAD-never-counts is pinned now, fully testable once P3.1 adds the click XADD) Host guard middleware (`PUBLIC_REDIRECT_HOST`). *Test:* on that host only `GET`/`HEAD /{code}` works. Also: explicit `HEAD /{code}` route that never enqueues a click (D12); create limiter fails closed (D13).
Done when: all the tests above pass against real Postgres+Redis.
Chart follow-up for P3 (D71, docs/RUNBOOK-p3-chart.md): worker Deployment, IP_HASH_SECRET and metrics ports in `linkforge-gitops`; written and rendered offline, NOT yet deployed by the human.
Chart follow-up (D64, docs/RUNBOOK-redis-chart.md): Redis StatefulSet + P2 settings in the linkforge-gitops repo; written and rendered offline, NOT yet deployed by the human.
Prompt:
> P2 only. Plan P2.1–P2.8 following ARCHITECTURE §2.1, §2.4, §5, §6. List the exact tests for each fail-open/fail-closed case. Wait for approval.
DevOps pointer: none. Be able to explain ARCH §8.

## P3 — Click pipeline, analytics API, metrics
- ☑ P3.1 (D65; needs `IP_HASH_SECRET` in the cluster Secret before deploy) `XADD` in redirect (bounded timeout, best effort, drop metric). *Test:* Redis down → 302 + dropped counter.
- ☑ P3.2 (D66; UA parsing is still a placeholder until P3.4) Worker per ARCH §2.3: group creation, batch with orphan pre-filter, idempotent insert, rollups, ack-after-commit. *Test:* duplicate event → 1 row; batch containing one orphan → others stored.
- ☑ P3.3 (D67) Per-event fallback, XAUTOCLAIM, extended-XPENDING poison detection, dead-letter (ack only after dead XADD succeeds), graceful SIGTERM; group created at `0` and re-created on NOGROUP. No replay command (D18; manual runbook). *Test:* **commit-then-crash-before-XACK → redelivery → still exactly one row and +1 count (D9)**; kill mid-batch → no double count; poison → dead stream; dead XADD failure → stays pending; NOGROUP after Redis wipe → worker recovers; events XADDed before the group existed are processed.
- ☑ P3.4 (D68) UA parsing + bot flag. *Test:* sample UAs.
- ☑ P3.5 (D69) Analytics endpoints (UTC `YYYY-MM-DD` labels, zero-fill, range semantics). *Test:* boundaries incl. partial today.
- ☑ P3.6 (D70) Metrics: all names in ARCH §7 incl. `click_stream_lag/pending/length`; API metrics on :9101, worker :9100; JSON logs + request ID. *Test:* scrape shows every metric name.
Done when: tests pass; `curl localhost:9101/metrics` and `:9100/metrics` show the full list; stopping the worker makes lag/pending grow and restarting drains them.
Prompt:
> P3 only. Plan P3.1–P3.6 per ARCHITECTURE §2.2–2.3, §3.1, §7. Include how each failure test is produced against real Redis/Postgres. Wait for approval.
DevOps pointer: §11.

## P4 — Frontend dashboard
- ☑ P4.1 Vite+TS+Tailwind+shadcn setup, API client (cookies + CSRF), router, protected routes.
- ☑ P4.2 Register/Login/Landing. ☑ P4.3 Links page (search/filter/pagination/create/edit/delete/copy). ☑ P4.4 Dashboard + Link analytics (Recharts, UTC date labels as-is). ☑ P4.5 Settings (API keys, change password). ☐ P4.6 loading/empty/error states + responsive checks. ☐ P4.7 Vitest + RTL tests; add the frontend job to CI.
Done when: lint, typecheck, test, build pass; manual run of PRD F1–F6 works against the real backend.
Prompt:
> P4 only. Read frontend/CLAUDE.md and use the frontend-design skill: this must look genuinely good (portfolio piece). Plan the design system + pages/components/hooks first; do not invent endpoints outside ARCHITECTURE §4. Wait for approval.
DevOps pointer: none.

## P5 — Containers + Compose (full stack; backend Dockerfile and basic compose already exist from P1S)
- ☐ P5.1 Extend the backend image/compose with worker + redis (AOF) and re-check size/non-root. ☐ P5.2 Real frontend Dockerfile (nginx-unprivileged, `nginx.conf` proxy, security headers). ☐ P5.3 `.dockerignore`s. ☐ P5.4 Compose: postgres, redis (AOF), one-shot migrate, api, worker, frontend :8080; healthchecks.
Done when: `docker compose up --build` from a clean clone gives a working app on :8080 including analytics; `docker compose exec api id` is non-root; image sizes noted.
Prompt:
> P5 only. Write Dockerfiles and compose per infra/CLAUDE.md with explanatory comments. Plan first.
DevOps pointer: §2 — run `docker history` and `trivy image` yourself.

## P6 — CI hardening + Terraform foundations + ECR push
- ☐ P6.1 Trivy `fs` + `image` gates; docker build in CI; `.trivyignore` policy. ☐ P6.2 Terraform `bootstrap/` (state bucket). ☐ P6.3 `foundation/` (GitHub OIDC provider + role, ECR repos immutable + lifecycle). ☐ P6.4 Extend `build-push.yml` (it already pushes to GHCR since P1S): add OIDC → ECR push of the same `:<short-sha>` image. kind keeps pulling GHCR, EKS pulls ECR (`envs/eks` points at ECR). Add Dependabot, Checkov on `infra/terraform`, gitleaks pre-commit (D19). Actions pinned by SHA. ☐ P6.5 Turn on branch protection with required checks.
Done when: merge to main yields an ECR image tagged with the SHA; a deliberately vulnerable base (test branch) fails Trivy; no AWS keys in GitHub secrets (only the gitops token); `terraform plan` shows no changes.
Prompt:
> P6 only. Write Terraform (bootstrap, foundation) and the build-push workflow per infra/CLAUDE.md. Do NOT run apply — give me exact commands. Plan first.
DevOps pointer: §3, §4, §5, §6.

## P7A — kind cluster + raw manifests (learn the objects)
Goal: run the app on kind with plain YAML before Helm.
- ☐ P7A.1 `infra/kind/kind-config.yaml`, create cluster, Traefik ingress, metrics-server (kubelet-insecure-tls on kind only). ☐ P7A.2 Raw manifests in `infra/k8s-raw/` (throwaway scaffolding): Postgres + Redis StatefulSets, migration Job, api/worker/frontend Deployments, Services, Ingress (two hosts), probes, requests/limits. Secrets created by hand with `kubectl create secret`.
Done when: all pods Ready; `app.localtest.me` and `go.localtest.me` work (or /etc/hosts); `kubectl delete pod` heals; you can explain each object (DEVOPS_GUIDE §7).
Prompt:
> P7A only. Write kind config and raw manifests (throwaway). Comment every object with what/why. I will run the kubectl commands. Plan first.
DevOps pointer: §7.

## P7B — Helm chart (in `linkforge-gitops`)
- ☐ P7B.1 Convert raw manifests to `charts/linkforge` with values (image repo/tag, ingress className, `postgres.enabled`, hosts, resources, `PUBLIC_REDIRECT_HOST`, `TRUSTED_PROXY_COUNT`). ☐ P7B.2 Add HPA, PDB, ServiceMonitors (named `metrics` ports), securityContext. ☐ P7B.3 `envs/kind/values.yaml` (the EKS file `envs/eks/values.yaml` is written in P9; never share one file, D6). Note: P1S already created a minimal chart; P7A/P7B EXTEND it to the full app (Redis, worker, frontend, HPA, PDB, ServiceMonitors).
Done when: `helm lint` clean; `helm template` output reviewed by you; `helm upgrade --install` on kind works; switching `ingress.className` renders correctly; `kubectl top pods` fills HPA metrics.
Prompt:
> P7B only, in linkforge-gitops. Convert the raw manifests into a Helm chart. Show me `helm template` output for review. Plan first.
DevOps pointer: §8.

## P7C — Argo CD + GitOps loop
- ☐ P7C.1 Install Argo CD (pinned version). ☐ P7C.2 Single `linkforge` Application with automated sync, prune, selfHeal (secrets still manual). ☐ P7C.3 Prove: change `image.tag` in git → rollout; `kubectl scale` → self-heal; bad tag → old pods keep serving → `git revert` heals.
Done when: those three proofs are done and written down (in `docs/RESULTS.md`).
Prompt:
> P7C only. Write the Argo Application manifest(s). I will install Argo CD and run the proofs myself. Plan first.
DevOps pointer: §9.

## P7D — Sealed Secrets + enforced ordering + migration hook
- ☐ P7D.1 Sealed Secrets controller + sealed secrets for DB/Redis/IP-hash. ☐ P7D.2 Convert to the root app-of-apps with sync waves exactly as ARCH §11; move migration to a Sync-hook Job (wave 0). ☐ P7D.3 Verify the two caveats in ARCH §11 (Application health in app-of-apps; SealedSecret timing). ☐ P7D.4 Back up the sealing key (DEVOPS_GUIDE §10) and do one restore exercise.
Done when: a **fresh** kind cluster + `kubectl apply -f argocd/root.yaml` brings the whole stack up in the right order with no manual sync; a deliberately failing migration halts the sync while old pods serve.
Prompt:
> P7D only. Implement ARCHITECTURE §11 ordering and Sealed Secrets. List what you will verify and how. Plan first.
DevOps pointer: §9, §10.

## P8 — Observability, load test, failure drills (on kind)
- ☐ P8.1 kube-prometheus-stack via Argo (pinned; short retention). ☐ P8.2 Grafana dashboard ConfigMap: redirect p50/p95/p99 (route-filtered), RPS, 5xx ratio, cache hit ratio, `click_stream_lag/pending/length`, pod restarts, HPA replicas, Redis memory. ☐ P8.3 PrometheusRules per ARCH §7.1 + Alertmanager → Slack (webhook in a sealed secret). ☐ P8.4 k6 scripts (DEVOPS_GUIDE §12): warm-cache, mixed/cache-miss, create — with rate limits raised via values for the test. ☐ P8.5 Run the drills table (§13), record before/after in `docs/RESULTS.md`.
Done when: dashboard shows live traffic; a real alert reaches Slack during a drill; HPA scales up under load and back down; every drill has its measurable outcome recorded.
Prompt:
> P8 only. Plan dashboard panels with the exact PromQL, alert rules, and k6 scenarios. Wait for approval.
DevOps pointer: §11, §12, §13.

## P8b — Pre-cloud verification checkpoint (gate before P9)
- ☐ P8b.1 Security checklist run (auth/CSRF/Origin/change-password-revokes-other-sessions/API-key revoke/URL validation/alias collisions/host guard/spoofed XFF) — all covered by automated tests, list them. ☐ P8b.2 Outage tests: Redis down, DB down, worker down — compare with ARCH §8. Also: worker crash-after-commit drill (no double count) and a forced `BGREWRITEAOF` under write load to measure Redis peak memory (D9, D11). ☐ P8b.3 Backup/restore drill (ARCH §10): dump from kind → restore into fresh Postgres → counts match. ☐ P8b.4 Resource budget check: `kubectl top` on kind vs ARCH §9.3; update the table with measured numbers. ☐ P8b.5 Latest Trivy results saved; no unexplained HIGH/CRITICAL. ☐ P8b.6 `max_connections` arithmetic redone for the chosen RDS size.
Done when: a short `docs/VERIFICATION.md` lists each check with evidence. Do not start P9 without it.
Prompt:
> P8b only. Produce docs/VERIFICATION.md by running/listing each check with evidence. Do not claim anything you didn't run.

## P9 — EKS via Terraform + deploy via Argo CD (time-boxed; cost starts)
Prereqs (you): Budget set; credit balance known (DEVOPS_GUIDE §14; credits expire 2027-04-28, D3 — schedule this phase no later than ~March 2027); domain bought (real money; check whether credits cover it); Route53 zone created in `foundation/` and the registrar's nameservers pointed at it ONCE; calendar reminder for the destroy; P8b done.
Dependency order (explicit): `foundation (state, OIDC, ECR, Route53 zone)` → `cloud: VPC → EKS → RDS → ACM (DNS-validated) → cluster access (your IP in endpoint allowlist) → AWS Load Balancer Controller (IRSA) → Argo CD → root app` → secrets sealed against THIS cluster's controller → app syncs by waves.
- ☐ P9.1 `cloud/` Terraform: VPC (single NAT), EKS (managed node group, 2× t3.medium — re-check budget; use 3 or larger if pods go Pending), RDS (private, db.t4g.micro, `random_password`), ACM + DNS records, LB controller IRSA. ☐ P9.2 `envs/eks/values.yaml`: `ingress.className=alb`, `DATABASE_SSL_MODE=verify-full`, `postgres.enabled=false`, RDS endpoint, ECR image URLs, `TRUSTED_PROXY_COUNT`. ☐ P9.3 Bootstrap script/steps for Argo CD + root app. ☐ P9.4 Destroy checklist file.
Done when: `https://app.<domain>` loads; `https://go.<domain>/<code>` redirects; `https://go.<domain>/api/v1/auth/me` → 404 (host guard); clicks show in analytics; Argo Healthy/Synced.
Fallback: Plan B (k3s on one EC2) in DEVOPS_GUIDE §14.
Prompt:
> P9 only. Write Terraform for `cloud/` (terraform-aws-modules for VPC/EKS; own modules for rds/dns). Do not apply. List cost drivers, the dependency order, and the destroy order. Plan first.
DevOps pointer: §5, §14, §15.

## P10 — Prove it on AWS (same sitting as P9)
- ☐ P10.1 k6 against the real URL (note where k6 ran). ☐ P10.2 Drills: pod kill, Redis kill, worker kill, bad tag rollback. ☐ P10.3 Screenshots (Grafana, Argo). ☐ P10.4 Numbers into `docs/RESULTS.md`. ☐ P10.5 Destroy: delete Argo apps first (removes ALB), then `terraform destroy` in `cloud/`, then the console checklist (DEVOPS_GUIDE §14).
Done when: RESULTS.md has real numbers + screenshots; console shows no leftover billable resources; Cost Explorer next day ≈ $0.

## P11 — Polish
- ☐ P11.1 README (pitch, diagram, screenshots, k6 numbers, "what breaks when Redis dies", 3-command local run, deploy steps). ☐ P11.2 2–3 min demo video. ☐ P11.3 Resume bullets using only measured numbers. ☐ P11.4 `docs/DECISIONS.md` (short ADRs) + runbooks (`deploy/rollback`, `recovery`). ☐ P11.5 Interview prep from ARCH §12 and DEVOPS_GUIDE §16. ☐ P11.6 LICENSE + tidy history.
- ☐ P11.7 **Optional [C] (PRD F8):** retention job `python -m app.retention` (K8s CronJob, 90 days). *Test:* old raw events deleted; daily rollups remain.
Done when: a stranger can clone, run `docker compose up`, and see the app in < 5 minutes from the README alone.

---

## Progress Log (Claude Code: append at the end of every session)
| Date | Phase | What was done | Commands run / tests passed / NOT verified | Next / blockers |
|---|---|---|---|---|
| 2026-11-05 | P0 (partial) | Repo moved to `~/linkforge` (WSL), first commit made; Python 3.12.3, Docker 29.8.2, git present; pip/venv/make/unzip installed after switching apt to https (plain-HTTP .deb downloads were 403 on the user's network) | User ran the commands; verified from WSL: `docker version`, `git log`, `python3 -m venv`, `pip3 --version`, `make --version`. NOT done yet: GitHub push, kind/helm/terraform/aws/k6/kubeseal/trivy/node installs (deferred to the phases that need them), AWS budget credit-handling check, Identity Center decision | Push to GitHub (user), then P1 |
| 2026-11-05 | P0.5 | Wrote `docs/openapi.skeleton.yaml` (14 paths, 18 schemas), `docs/TRACEABILITY.md` (PRD → task → planned tests), and decisions D23–D35 in `docs/DECISIONS.md` for gaps the specs were silent on | Validated the OpenAPI file with `openapi-spec-validator` in a temp venv (valid 3.1.0); read all specs end to end. NOT verified: that the contract matches real code (nothing implemented yet); test names are plans, not existing tests; nothing committed | User reviews D23–D35 and the contract, then commit; start P1 |
| 2026-11-05 | P1 (P1.1–P1.7) | Backend core: FastAPI app + env-only config + health probes; 5 models + hand-written async Alembic migration; URL/alias/short-code validation; link CRUD with temporary dev user (D37); GET/HEAD `/{code}` redirect on Postgres; compose (Postgres 16.4 + Redis 7.4 AOF, ports overridable); `ci.yml` (backend job with service containers, non-blocking pip-audit and gitleaks, actions pinned by SHA). Decisions D36–D46. Renamed the walking-skeleton phase P1.5 -> **P1S** (clashed with the P1.5 redirect task) | Ran locally in WSL: `ruff check`, `ruff format --check`, `pytest -q` = 225 passed (real Postgres); mutation checks on ownership, expiry boundary, PATCH semantics, redirect precedence/302/503; live curl incl. Postgres stop/start; `alembic check`; `actionlint` on ci.yml (clean); the CI backend job rehearsed from a clean `git archive` checkout in a fresh venv (225 passed); `pip-audit` (no known vulnerabilities); pinned gitleaks over history (no leaks). NOT verified: the workflow on GitHub itself (needs `git push`), Redis (no code uses it yet), a hung (not refused) Postgres, latency, the OpenAPI contract vs generated spec | `git push`, watch Actions; then P1S (walking skeleton) |
| 2026-11-06 | P1S.1–P1S.3 | Backend + placeholder frontend images (multi-stage, non-root, digest-pinned, Trivy clean), compose api/migrate/frontend, `image-scan` composite action, `ci.yml` images job, `build-push.yml` (CI, build, scan, GHCR push, gitops tag bump). D47–D49. | Ran: docker builds, compose up, curls, read-only runs, Trivy gate (passes both images, fails a known-bad image), bump script rehearsal on a scratch repo, YAML parse of all workflows. NOT verified: actionlint (binary could not be checksum-verified, so not run), the real GHCR push, `GITOPS_TOKEN`, the gitops commit. | P1S.4 kind + Traefik + Helm chart |
| 2026-11-06 | P1S.4–P1S.5 (walking skeleton complete) | kind + Traefik + metrics-server, throwaway raw manifests, Helm chart (`linkforge-gitops`), Argo CD Application with sync waves, Trivy-gate fix (D54), docs/RUNBOOK-*.md, docs/RESULTS.md. D50–D55. | User ran and pasted outputs for: kind/Traefik, raw-manifest drills, `helm install` + drills A–E, Argo install, sync-wave deploy (no failed migrate pod, Synced/Healthy), proof 1 (push deployed), proof 2 (self-heal), proof 3 (bad tag, then `git revert`, Healthy). I ran: docker stop timing/exit-code tests on the api image (D55). NOT verified: proof 1 page text and timings (not captured), Kubernetes-side SIGTERM timing, anything under load, EKS. | P2 plan (system redis-server disabled; `.env.example` created by the user) |
| 2026-11-06 | P2.1-P2.8 (Redis, cache, limits, auth, keys, host guard) | SafeRedis (100/200 ms bounds, fallbacks); redirect cache-aside + negative cache + DEL-on-write; Lua rate limiter + trusted-proxy client IP; argon2id + Redis sessions + login limiter; CSRF + Origin + change-password; real auth on links (dev user removed); API keys + per-user create limiter; host guard. D56-D63. | Ran: ruff (check + format), full pytest = 436 passed against real Postgres and Redis (db 15, flushed per test); mutation checks per task (each task 7-12 deliberate breakages, all killed except 2 equivalent mutants in the host guard, see D63); live checks against the rebuilt compose stack (cache hit/miss/edit, 429 with spoofed XFF, register/login/CSRF/403s, API key create/use/revoke, change-password revoking a second device, host guard on a go host). NOT verified: CI image build + Trivy for the new dependencies (argon2-cffi, redis, prometheus_client), anything on kind (the chart still lacks Redis, REDIS_URL, TRUSTED_PROXY_COUNT=1, ALLOWED_ORIGINS, PUBLIC_REDIRECT_HOST, IP_HASH_SECRET), real browser CSRF behaviour (no frontend yet), latency or load. Known gaps (specs silent): no limiter on register or change-password, login limit is per IP+email only, no cap on API keys per user | DevOps follow-up: Redis StatefulSet + chart config for kind; then P3 (click pipeline) |
