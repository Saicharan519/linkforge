# Verification before the cloud (P8b)

Date: 2026-11-07. Everything here was run in this session against the local stack (backend tests against real Postgres and Redis,
the rest against the kind cluster). Where something was NOT verified, it says so. Numbers for load and drills live in `docs/RESULTS.md`
(P8.4, P8.5); this file is the checklist that points at the evidence.

## Summary

| Item | Result |
|---|---|
| P8b.1 security checklist | Covered by automated tests (listed below). Full suite: **707 passed** on the second run; **the first run had 1 failure** (a timing flake, see Findings). `ruff check` and `ruff format --check` clean. |
| P8b.2 outage tests | Redis down, worker down, Postgres down all run live and compared with ARCHITECTURE §8: behaviour matches. One real defect found (a 500 on login at the instant Postgres is killed). |
| P8b.3 backup and restore | Dump of the kind database restored into a throw-away Postgres: every table count matches, rollups equal non-bot events, the API image served a redirect (302) from the restored copy. |
| P8b.4 resource budget | Measured and compared with ARCHITECTURE §9.3 (table below and added to §9.3). API and worker are CPU-throttled at their 500m limits under load. |
| P8b.5 Trivy | Our images and repository files clean at the CI gate. Third-party images have findings: **Postgres 16.4 has 166 HIGH/CRITICAL with a fix** (decision needed); monitoring images in RUNBOOK-monitoring.md; Sealed Secrets controller 1 accepted HIGH (D78). |
| P8b.6 connections | Arithmetic redone: 44 connections at the HPA maximum. Fits kind (max 100, measured). For RDS db.t4g.micro it fits only if the real limit is at least about 74; **not verified** (no AWS access, check with `SHOW max_connections;` in P9). |

## P8b.1 Security checklist (automated tests, real Postgres and Redis)

`cd backend && .venv/bin/python -m pytest -q` collected 707 tests. Each control below has tests; names are the real ones (grep a keyword in `backend/tests`).

| Control | Tests (examples) |
|---|---|
| CSRF double-submit and Origin check | `test_managing_keys_needs_a_session_csrf_and_origin`, `test_revoking_needs_csrf_and_a_session`, `test_csrf_header_without_the_cookie_is_403`, `test_login_refuses_a_cross_site_browser_request_without_origin` (36 tests match csrf/origin) |
| Change password ends every OTHER session, keeps this one | `test_change_password_ends_every_other_session_but_not_this_one`, `test_change_password_switches_the_password_and_keeps_this_session`, `test_password_change_that_cannot_revoke_sessions_is_reported_as_503` |
| API key revoke is immediate; plaintext shown once; only a hash stored | `test_a_revoked_key_is_401_immediately`, `test_create_returns_the_plaintext_once_and_stores_only_a_hash`, `test_the_plaintext_can_never_be_read_again`, `test_changing_the_password_does_not_revoke_api_keys` |
| Destination URL validation | `test_invalid_destination_422[javascript:alert(1)]`, `[not a url]`, and the parametrised unit tests in `tests/unit/test_urls.py` (77 tests match) |
| Alias collisions and reserved words | `test_duplicate_alias_409`, `test_alias_case_preserved`, `test_prefix_collisions_are_retried` (58 match) |
| Host guard on the public short-link host | `tests/integration/test_host_guard.py`, e.g. `test_everything_else_is_a_plain_404_on_the_public_host[...]`, `test_x_forwarded_host_is_ignored` |
| Spoofed `X-Forwarded-For` | `test_spoofed_x_forwarded_for_does_not_reset_the_login_limit`, `test_spoofed_x_forwarded_for_is_ignored_without_a_trusted_proxy`; also LIVE in P8.4 (150 spoofed requests: exactly 120 x 302 and 30 x 429) |
| Login fails closed, redirect fails open | `test_login_fails_closed_when_redis_is_down`, `test_login_with_redis_down_never_reveals_whether_the_password_was_right`, `test_redis_down_fail_open_lets_the_request_through`, `test_creating_links_fails_closed_when_redis_is_down_but_redirects_and_key_reads_work` |
| Cross-user access is a 404 | `test_another_users_link_and_an_unknown_id_are_the_same_404`, `test_a_key_only_sees_its_owners_links` |
| HEAD never counts a click | `test_head_never_queues_a_click` |
| Password hashing argon2id | `test_register_creates_an_account_with_an_argon2id_hash`, `test_password_hasher_uses_the_rfc9106_low_memory_profile` |
| Sessions, expiry, 401 with cleared cookies | `test_unknown_and_expired_sessions_are_401_with_cleared_cookies`, `test_redis_down_is_503_not_401_and_cookies_are_kept` |
| Worker: idempotency, redelivery, orphans, dead letters | `tests/integration/test_worker_batch.py`, `test_worker_failures.py` (incl. `test_commit_then_crash_before_xack_is_redelivered_without_double_counting`) |

Not verified here: the frontend suite (167 tests, last run in P4/P7 CI), browser-level CSRF behaviour in a real cross-site page, and any
penetration-style testing. CI on the pull requests runs the same backend suite plus dependency audit and gitleaks.

## P8b.2 Outage tests against ARCHITECTURE §8

| What dies | ARCH §8 says | Measured (RESULTS.md P8.5, plus the Postgres run below) |
|---|---|---|
| Redis | redirects work (Postgres fallback), logins fail closed, clicks dropped and counted, AOF restores | 100% of redirects 302, logins 503, 595 of 24,511 clicks lost and equal to `click_events_dropped_total`, Redis back in 6 s, consumer group kept |
| Worker | redirects and API work, lag/pending grow, drain after restart | lag grew about 105/s for 6.5 min, drained in 42 s, 5 of 39,895 clicks unaccounted |
| Postgres | cache hits still redirect, misses give 503, logins fail, events accumulate and are processed when the DB returns | `postgres-0` deleted with grace period 0 under a probe (Ready again after 5 s): cached redirect 302, uncached code 503, login 503; afterwards all 40 hot clicks stored (queued 40, stored 40), no worker or API restarts. **Defect:** one login request got a **500** at the moment of the kill (the API log shows an unhandled `rollback` error on a connection that was torn down mid-request, instead of the handled "postgres unavailable" 503). The outage was only 5 s long, so the longer-outage behaviour (events piling up in the stream while the DB is away) was NOT exercised. |
| One API pod | Service routes around, PDB keeps one | 2 of 24,000 requests failed (0.008%) |
| Worker crash after commit | one row per `event_id`, rollup once | automated test only (not repeated under load) |

## P8b.3 Backup and restore drill (ARCHITECTURE §10)

Scripts added in this phase: `scripts/pg_dump.sh` (dump of the kind database, custom format, runs inside the pod) and
`scripts/pg_restore_check.sh` (restore into a throw-away container of the same Postgres image, compare counts, run the API image against it).
Run on 2026-11-07, dump 11.9 MB:

| Table | Restored | Live |
|---|---|---|
| users | 1 | 1 |
| links | 1215 | 1215 |
| api_keys | 1 | 1 |
| click_events | 371,147 | 371,147 |
| link_daily_stats | 1010 | 1010 |

Rollup sum 366,954 equals the non-bot `click_events` (4,193 bot clicks, all from the `curl` probes, are stored but excluded from rollups by design, ARCH §2.3).
The API image (`sha-e294aa6`) started against the restored database: `/readyz` 200 and `GET /r1cDcAv` answered 302 to its destination (with Redis deliberately unreachable).
Not verified: restore of an RDS snapshot (P9); restoring the SealedSecrets key (the human owns the off-machine copy); a restore after a Postgres major upgrade.
(The script's first version compared the rollup with ALL events, failed, and the cause was checked: that was my invariant being wrong, not the data.)

## P8b.4 Resource budget (kind, idle, 2 api pods, 2026-11-07) against ARCHITECTURE §9.3

| Workload | Estimate in §9.3 (request) | Measured idle | Measured under load | Note |
|---|---|---|---|---|
| api (x2, HPA to 6) | 100m / 256Mi | 5m / 66-70 Mi | up to 0.53 cores, 71 Mi per pod | CPU-throttled at the 500m limit (49% of periods at 6 pods); memory request is about 3.5x what it uses |
| worker | 100m / 128Mi | 2m / 63 Mi | 0.5 cores (throttled 73% of periods), 64 Mi | drains about 950 events/s |
| frontend | 25m / 32Mi (x2) | 1m / 10 Mi | not loaded | one replica on kind (chart value), the estimate assumed 2 |
| redis | 100m / 256Mi (limit 512Mi) | 23m / 39 Mi | 240 MiB container peak with 214.8 MiB of data during an AOF rewrite | the 512Mi limit leaves about 270 MiB of headroom at the alert line |
| postgres | 100m / 256Mi | 33m / 119 Mi | 0.14 cores peak | |
| Argo CD (4 components) | about 500m / 1 Gi | about 17m / 400 Mi | | no resource requests are set in the installed manifest |
| monitoring stack | about 700m / 1.5 Gi | about 80m / 1.0 GiB (Prometheus 491, Grafana pod 435) | | |
| everything | | node: 757m CPU, 3.9 GiB of 7.8 GiB (50%) | | sum of requests 1.85 cores / 2.5 GiB; kube-apiserver alone 937 Mi |

Not verified: the EKS node fit (t3.medium, pod-count limit): needs P9. The estimates for AWS LB Controller and EBS CSI cannot be measured on kind.

## P8b.5 Trivy (0.76.2, gate flags: HIGH,CRITICAL, `--ignore-unfixed`, exit 1)

| Target | Result |
|---|---|
| `linkforge-api:sha-e294aa6` (scanned from the registry) | clean, exit 0 |
| `linkforge-frontend:sha-e294aa6` | 0 findings, exit 0 |
| repository files (`trivy fs`, with `.trivyignore`) | clean, exit 0; no active `.trivyignore` entries |
| `postgres:16.4@sha256:e62fbf...` | **166 HIGH/CRITICAL with a fix** (106 in Debian packages: 89 HIGH + 17 CRITICAL; 60 in the bundled `gosu` Go binary: 56 HIGH + 4 CRITICAL). The pinned tag is old: 16.15 exists. NOT gated by CI (third-party). **Needs a decision**: bump the minor version in the chart (same major, in-place upgrade). |
| `redis:7.4@sha256:2e3eb1...` | 0 |
| `traefik:v3.7.16` | 2 (go-jose CVE-2026-116587, x/net CVE-2026-78661) |
| Sealed Secrets controller 0.41.2 | 1 HIGH x/net CVE-2026-78661, **accepted** in D78 (no fixed release yet) |
| monitoring images (8) | counts in `docs/RUNBOOK-monitoring.md` (54/16/11/5/2/2/1/1), recorded, not gated |

A first scan of the API image failed with a layer-analysis error when Trivy read it from the local Docker store; `--image-src remote` worked (CI scans differently, so this is a local-only quirk, cause not investigated).
CI also keeps the temporary zlib/musl `apk upgrade` block in `frontend/Dockerfile` until upstream nginx is rebuilt (see TASKS).

## P8b.6 Postgres connection budget

Formula (ARCHITECTURE §9.2): `C_total = N_api x (pool + overflow) + N_worker x (pool + overflow) + C_migration + C_admin`.
At the HPA maximum: 6 x (3 + 2) + 1 x 3 + 1 + 10 = **44**.

- **kind:** `SHOW max_connections` = 100 (3 reserved). 44 is under 60% (60). Measured now: 6 connections (idle, 2 pods), peak with 6 pods NOT measured.
- **RDS db.t4g.micro (1 GiB):** AWS's default is `LEAST(DBInstanceClassMemory / 9531392, 5000)`. With the whole 1 GiB that would be about 112, but `DBInstanceClassMemory` is smaller than the instance memory, so the real value is lower (roughly 85-90 by my recollection, NOT checked). 44 must stay under 60% of it, i.e. the limit must be at least 74. That holds for the commonly quoted figures but this is **derived, not measured**: run `SHOW max_connections;` on the real instance in P9 and redo the sum if HPA maximum or pool size change.

## Findings and open decisions

**Update (same day, D84): findings 1-3 below were fixed in a follow-up; see the "Resolution" lines.**

1. **Flaky test:** `test_commit_then_crash_before_xack_is_redelivered_without_double_counting` failed once in the first full run (`reclaim()` returned 0, expected 2) and passed in the second full run and in 6 of 6 isolated runs, with the compose worker both running and stopped (the tests use Redis DB 15, the compose app DB 0, so they do not interfere). Probable cause: it waits only 0.1 s for the entries to become idle. It is a CI risk; fixing it is a test change (not done).
   **Resolution (D84):** NOT reproduced in 36 further runs (30 isolated, 6 with the worker files), so the cause is a best guess. The five fixed `sleep(0.1)` waits before `reclaim()` were replaced by `wait_idle()`, which asks Redis for the idle time of the pending entries, and the assertion now prints what was pending. Full suite 710 passed.
2. **500 on login when Postgres is killed mid-request** (above).
   **Resolution (D84):** `get_db` closed the session in an `async with`; the close rolls back, the rollback raised on the dead connection, and an error during dependency teardown replaces the response. It now catches a failed close, logs it and invalidates the connection. Three new tests make the close fail like the real one: they failed (500) before the fix and pass after. **Verified live afterwards:** with image `sha-32c5f05` running, `postgres-0` was deleted six times (grace period 0) under about 100 login requests: 88 x 401, 11 x 503, 1 timeout, **0 x 500**, no API restarts, and the new `db session close failed` warning was logged by 5 of the 6 API pods, i.e. the exact failure that used to be a 500 happened and was absorbed. (A race like this is not proven gone by six tries; the unit tests are the guard.)
3. **Postgres image 16.4 is out of date** (166 findings with fixes).
   **Resolution (D84):** kind runs `postgres:16.15` (tag and digest pinned). Trivy: 26 HIGH/CRITICAL with fixes, all in the bundled `gosu` binary (Debian packages are clean). Data intact after the upgrade (all 5 table counts equal). The new image is Debian 13 (glibc 2.41, the old data was built under 2.36), so Postgres warned about a collation version mismatch; I ran `REINDEX DATABASE` (6 s) and `ALTER DATABASE ... REFRESH COLLATION VERSION` on `linkforge`, `postgres` and `template1`: no invalid indexes, emails and codes still unique. A fresh cluster is not affected. CI and docker-compose also moved to 16.15 (an existing compose data volume needs the same REINDEX, or `docker compose down -v`).
4. The compose stack from P5 (api, worker, frontend, postgres, redis containers) is still running on this machine; I briefly stopped and restarted only its worker during finding 1.
5. Pull request #7 (k6 scripts) and the pull request with the two scripts above were opened but not necessarily merged when this was written; see the repository.
