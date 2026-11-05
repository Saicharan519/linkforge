# LinkForge — Decisions (ADR log)

Short records of decisions made during design review (2026-11-05). Each says WHAT, WHY, and what it changes. Newer decisions override older text elsewhere only if this file is updated to say so.

| # | Decision | Why | Where it lands |
|---|---|---|---|
| D1 | Working mode: Claude writes backend/frontend code + tests; for DevOps phases Claude writes commented files, explains what/why/how, gives exact commands + expected output + a "break it" exercise; the human runs them | Goal is a DevOps placement project the human can defend in interviews | CLAUDE.md |
| D2 | Kubernetes targets: **kind** (local, first) → **EKS** (primary cloud, one time-boxed session) → **k3s on EC2** (documented fallback, NOT built unless EKS is blocked) | EKS gives the AWS DevOps signal; k3s is the cost fallback | TASKS P9, DEVOPS_GUIDE §14 |
| D3 | Cloud deadline = AWS credit expiry **2027-04-28** (balance seen 2026-11-05: $96.23). Run the EKS session well before (target ≤ March 2027). EKS has no pause: create → use → destroy | Credits are dollars; control plane bills until the cluster is deleted | TASKS P9/P10 |
| D4 | **Walking skeleton right after P1**: Dockerfile, Compose, CI, kind, minimal Helm chart, Argo CD for a redirect-only app with a placeholder nginx | De-risk DevOps early; a complete CI→GitOps→kind loop exists before features pile up | TASKS P1.5 |
| D5 | **Images:** CI pushes to **public GHCR** (GITHUB_TOKEN, no AWS) from P1.5; kind pulls GHCR; `kind load` only for local iteration. **ECR + OIDC** is added in P6 and used by EKS | Private ECR can't be pulled by a local kind cluster without credentials; GHCR keeps the full CI→gitops→Argo loop working on kind for free | infra/CLAUDE.md, TASKS P1.5/P6 |
| D6 | Helm values per environment: `envs/kind/values.yaml`, `envs/eks/values.yaml`. Never one shared file. k3s (if ever) gets its own file | kind and EKS differ in ingress class, DB, image repo | gitops repo, DEVOPS_GUIDE §8/§9 |
| D7 | Worker consumer group created at `0` (not `$`) and re-created on NOGROUP | Events XADDed before the group exists, or after a Redis wipe, must not be silently lost | ARCH §2.3 |
| D8 | Poison detection uses extended `XPENDING ... IDLE` (XAUTOCLAIM does not return delivery counts). Consumer name = pod name. Never `XGROUP DELCONSUMER` a consumer with pending entries: XAUTOCLAIM first, confirm zero pending, then delete | DELCONSUMER discards that consumer's pending entries | ARCH §2.3 |
| D9 | Crash between DB commit and XACK is a REQUIRED test: redelivery hits `ON CONFLICT DO NOTHING`, `RETURNING` is empty, rollup is NOT incremented | Core idempotency guarantee | ARCH §2.3, TASKS P3.3 |
| D10 | Argon2: `Semaphore(2)`; API memory request 256Mi / limit 384Mi | 4 × 64 MiB would exceed the old 192Mi budget | ARCH §6.2, §9.3 |
| D11 | Redis: container limit 512Mi with `maxmemory 256mb` as a starting point; MEASURE peak during a forced AOF rewrite (P8b) and adjust | AOF rewrite forks; copy-on-write can spike memory | ARCH §5.1, §9.3 |
| D12 | `HEAD /{code}` is supported (explicit route), returns same status/Location, no body, and is NEVER counted as a click | Compatibility with `curl -I` and link unfurlers without inflating analytics | PRD F3, ARCH §2.1 |
| D13 | Create-link limiter **fails closed** if Redis is down | Low-volume write path; redirects are unaffected | ARCH §5 |
| D14 | RDS connections use TLS with certificate verification (`verify-full`, RDS CA bundle baked into the image) | Encryption without verification is weak | ARCH §6.4, §9.1 |
| D15 | One uvicorn process per pod; scale with replicas | Per-process metrics and the metrics server assume it | ARCH §1 |
| D16 | New worker env vars: `WORKER_BATCH_SIZE, WORKER_BLOCK_MS, WORKER_CLAIM_IDLE_MS, WORKER_CLAIM_INTERVAL_SECONDS, MAX_DELIVERIES, CLICK_DEAD_MAXLEN` (+ `DATABASE_SSL_MODE`, `DATABASE_SSL_CA_FILE`) | Tests must not wait 60 s for reclaim; nothing hard-coded | ARCH §9.1 |
| D17 | `total_clicks` = SUM of `link_daily_stats` (bots excluded), computed with one grouped query per page of links | No N+1 | PRD F2, ARCH §3 |
| D18 | **Scope cuts:** no session list/revoke UI or endpoints (change-password still revokes other sessions); no `replay-dead` command (manual runbook instead). **Kept:** polished Landing + real UI design | User wants a good-looking portfolio piece; backend breadth is not the goal | PRD F1/F5, TASKS, CLAUDE.md |
| D19 | Extra DevSecOps: Dependabot, Checkov on Terraform in CI, gitleaks pre-commit | Cheap, high signal; pinned versions otherwise rot | infra/CLAUDE.md, DEVOPS_GUIDE §3 |
| D20 | Shell = WSL2 (Ubuntu, already installed with Docker); repo lives in the WSL filesystem at `~/linkforge`, outside OneDrive (user confirmed); `.gitattributes` forces LF; `.wslconfig` caps WSL at 8 GB | Laptop has ~16 GB with Windows already using ~12 GB; OneDrive breaks `.git`/`node_modules`/`.terraform` | TASKS P0, DEVOPS_GUIDE |
| D21 | Secrets stay Sealed Secrets. External Secrets / Secrets Manager remain "production would use" only | Stack is locked; avoids scope creep | unchanged |
| D22 | `.claude/settings.json` deny rules are a guardrail, not a boundary. The real control: cloud credentials never exist in a shell Claude runs in | Pattern denies are bypassable (`--context`, `-chdir`, `cat .env`) | CLAUDE.md, SECURITY.md |
