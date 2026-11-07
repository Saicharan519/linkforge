# Results and evidence

Measured results and drill outcomes: this is the part of the repo a reviewer reads to see that
things were actually run and observed. Fill in the "Observed" column from your own terminal;
do not copy my predictions. Empty cells mean "not yet run".

## Walking skeleton (P1S): the GitOps loop

| # | Proof | What I did | Observed (output / time) | Date |
|---|---|---|---|---|
| 1 | A push deploys with no manual step | Changed the placeholder page text and pushed (`docs/RUNBOOK-argocd.md` proof 1) | Push `8c64e1e` to linkforge; CI then added bump commit `046945f` to linkforge-gitops (tag `sha-8c64e1e`); Argo rolled both deployments with no manual step (api and frontend pods were replaced, migrate Job re-ran and `Completed`). Timings and the page-text check were not captured | 2026-11-06 |
| 2 | Self-heal | `kubectl scale deploy/api --replicas=5`, then deleted `deploy/frontend` (proof 2) | Extra api pods (`5kj7t`, `7n2rl`, `pt78j`) went to `Terminating` about 13 s after the scale, back to 2 api pods; they ended as `Error` at about 31 s (see note below). `deploy/frontend` deleted; a frontend pod was running again afterwards (recreation timing not captured) | 2026-11-06 |
| 3 | Bad release keeps old pods serving; `git revert` heals | Set `sha-0000000` as the frontend tag, then `git revert` (proof 3) | New pod `frontend-869849d94d-fnnh7` stuck in `ImagePullBackOff` (0/1) while old pod `frontend-7ccbb9c8f4-2tbv2` stayed `1/1 Running`; `curl http://app.localtest.me/` still returned `<h1>LinkForge</h1>`. Healed with `git revert --no-edit HEAD && git push` (commit 1b75529), no kubectl. Post-revert Argo status: not yet recorded | 2026-11-06 |

Timings worth writing down for #1: push to green CI, CI bump commit to Argo sync, sync to new
pod Ready, and the total.

## Kubernetes drills (P1S.4b/4c)

| Drill | Observed |
|---|---|
| Delete an API pod: requests keep succeeding | Loop printed `302` throughout (screenshot, 2026-11-06) |
| Scale Postgres to 0: readiness fails, no restarts | `503`s while Postgres was down, `302` again after it returned; restarts stayed 0 |
| Plain Helm upgrade with a new image tag fails on the immutable Job | Revision 4 `failed`; a new api pod in `ImagePullBackOff` while 2 old pods served; fixed with `helm rollback linkforge 3` |
| CI gate caught a new CVE on its own | `build-push` for 2ea2fc9 failed at the image-scan step (frontend); no image pushed, no gitops bump. Reproduced locally: zlib CVE-2026-85091 HIGH, fixable. Fixed with a temporary `apk upgrade zlib` layer; both images then scan clean (D54) |
| Pod right after `helm install` (no ordering): migrate Job failed once, then passed | `migrate-5pvsm` Error (DNS name for the headless Service did not exist yet), `migrate-nb524` Completed |
| Same install under Argo CD with sync waves | Application went straight to `Synced`/`Healthy`; one `migrate-f95lc` pod, `Completed`, no failed pod; `postgres-0` Running before it; `go.localtest.me/Helm01` returned `302` (data survived the handover). Ingress did not stay Progressing (Traefik `ingressEndpoint.ip` change worked) |

Note on proof 2: the scaled-up api pods were removed while still starting (`0/1`) and ended as
`Error` about 31 s later. Explained and reproduced with Docker (D55): a container's PID 1 ignores
SIGTERM until the app installs a handler, so a pod killed during Python start-up is only stopped
by SIGKILL when the grace period ends (exit 137). A READY api container stops in about 0.7 s with
exit 0 and a clean "Application shutdown complete" log. The Kubernetes-side timing was not
measured directly.

## P2 on kind (Redis chart follow-up, docs/RUNBOOK-redis-chart.md)

| Drill | Observed |
|---|---|
| Redis pod deleted: redirects keep working, login fails closed, session survives the restart | Checked once a second from 16:12:08: redirect `302` on all 20 lines; login `503` for the first 6 lines (about 8 s while Redis restarted), then `200`. The cookie from before the restart still returned the user afterwards (the AOF on the volume restored the session). 2026-11-06 |
| Required setting forgotten in git: Argo shows ComparisonError, cluster unchanged, `git revert` heals | Deleted `ALLOWED_ORIGINS`: app `Sync Unknown / Health Healthy` with the message `config.ALLOWED_ORIGINS is required (e.g. http://app.localtest.me)`; the api, frontend, redis and postgres pods were unchanged (api age 17 m). `git revert` + push gave `Synced / Healthy`. 2026-11-06 |
| Settings-only change rolls the API pods (checksum/config) | Added `LOG_LEVEL: DEBUG` (no image change): the migrate Job re-ran, then `api-648cfdbb47-*` pods replaced `api-767df76d87-*` one at a time (the new pod was Ready before the old one terminated; old pods ended `Completed`). 2026-11-06 |
| 130 requests through Traefik to the go host: about 120 x 302 then 429 | |
| go host answers 404 for /healthz and /api/v1/links; app host unrestricted | |

## P3 on kind (click worker chart follow-up, docs/RUNBOOK-p3-chart.md)

| Drill | Observed |
|---|---|
| Rollout order: secret key, chart (worker off), application, worker on; old pods serving throughout | NOT followed (the application was pushed before the chart), which exposed two real problems. (1) The new image's migrate hook failed with `ip_hash_secret Field required`: Alembic loads the app Settings and my chart had not given the migration Job the secret (fixed in 8a379c2, D71). The old API pods (P2 image) kept serving the whole time (`maxUnavailable: 0`), the new API never rolled. (2) After the fix was pushed the migrate pods kept failing because Argo retries a running sync against the commit it started with (op revision 91adc3e, desired 8a379c2); `kubectl -n argocd patch app linkforge --type merge -p '{"operation":null}'` started a fresh sync: migrate `Completed`, api and frontend rolled to `sha-b5da595`, app `Synced / Healthy`. Worker then enabled with a one-line commit. 2026-11-06 |
| Analytics after clicks with different browsers (bot stored but not counted, referrer host only) | 3 desktop Chrome clicks with a `Referer` carrying `?token=SECRET`, plus one `curl/8.5.0`: analytics `total 3`, referrers `news.example.com` (host only), devices desktop 3, browsers Chrome 3; worker `click_events_processed_total{status="ok"} 4` (3 people + 1 stored bot). 2026-11-06 |
| Worker switched off the GitOps way: lag grows on the API metrics port, drains when switched on | Worker off (`worker.enabled: false` in git) and 10 browser clicks: API port showed `click_stream_lag 10.0`, `pending 0.0`, `length 14.0`, analytics still `total 3`. Switched on again: 25 s later the gauge still read 10 (the worker pod was still starting behind the re-run migration hook); about a minute later `lag 0.0`, `pending 0.0`, `length 15.0` (14 + the malformed message of the next drill); Postgres: 13 human rows + 1 bot row, daily counter 13: nothing lost, nothing counted twice. 2026-11-06 |
| A malformed message ends in clicks:dead and click_events_processed_total{status=dead} +1 | `XADD clicks * event_id not-a-uuid link_id nope`: within 3 s `clicks:dead` held one entry with the original fields, `original_id`, `error malformed: KeyError: 'ts'` and `failed_at` 2026-11-06T19:03:43Z. 2026-11-06 |
| Redis pod deleted: the worker stays Running and storing resumes | `redis-0` back `1/1` in 3 s; `worker-...` stayed `1/1 Running` with 0 restarts throughout. 2026-11-06 |
| /metrics is not reachable through any Ingress host | `go.localtest.me/metrics` 404 (host guard), `app.localtest.me/api/metrics` 404 (the API has no such route), `app.localtest.me/metrics` the placeholder HTML page (the frontend, not Prometheus text). The metrics are only on the pod ports 9101/9100 via port-forward. 2026-11-06 |

## Resource use on kind (validates ARCHITECTURE §9.3 estimates)

Paste `kubectl top pods -n linkforge` and `kubectl top nodes` here, idle and (later) under load:

Idle, walking skeleton under Argo CD (2026-11-06; Argo's own pods are not included):

```
$ kubectl top pods -n linkforge
NAME                       CPU(cores)   MEMORY(bytes)
api-7984ddfc86-26mf7       4m           57Mi
api-7984ddfc86-8frvg       4m           56Mi
frontend-9d5ccb5dd-h5pfg   1m           10Mi
postgres-0                 31m          26Mi

$ kubectl top nodes
NAME                      CPU(cores)   CPU(%)   MEMORY(bytes)   MEMORY(%)
linkforge-control-plane   410m         3%       2484Mi          31%
```

Under load: not yet measured (P8 / k6).

With the P3 stack on kind (API x2, frontend, Postgres, worker; 2026-11-06, idle):

```
$ kubectl top pods -n linkforge
NAME                        CPU(cores)   MEMORY(bytes)
api-8c97c5548-btm7x         4m           69Mi
api-8c97c5548-jfcf6         6m           67Mi
frontend-74bf984895-qhqsl   1m           11Mi
postgres-0                  35m          30Mi
worker-7c8c8f4d97-2n7sb     3m           62Mi
(redis-0 had just been restarted and had no sample yet)

$ kubectl top nodes
linkforge-control-plane   597m   4%   2403Mi   30%
```


## P5: images and the full compose stack (2026-11-07)

Run from a clean `git clone` of commit 264ae81 with `docker compose -p lfclean up -d --build --wait` (own volumes, removed afterwards).

| What | Result |
|---|---|
| All services healthy | postgres, redis, api, frontend healthy; migrate exited 0; worker up (its healthcheck is disabled on purpose) |
| Image sizes | `linkforge-api:dev` 324 MB, `linkforge-frontend:dev` 33.6 MB |
| Frontend image contents | nginx 1.30-alpine-slim + 1.88 MB of built app; no Node, no sources; musl and zlib upgraded (temporary, see Dockerfile) |
| Users (`id` inside the containers) | api and worker `uid=10001(app)`, frontend `uid=101(nginx)`; none is root |
| Security headers on `/` and `/assets/*` | CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy present on both; `/` is `no-cache`, hashed assets `max-age=31536000, immutable` |
| SPA fallback / missing asset | `/app/links` 200 (index.html), `/assets/nope.js` 404 |
| `/api` through nginx | `GET /api/v1/auth/me` without a session: 401 JSON from the API; register 201, login 200, create link 201, all through :8080 with the CSRF and Origin rules |
| Redirect and analytics | `GET :8000/cleanck` 302 to the destination; 3 clicks with a referrer plus 1 without: total 4, referrers news.example.com 3 and (direct) 1, device desktop, browser Chrome (through the worker, read back through the proxy) |
| Redis | `appendonly yes`, `maxmemory-policy noeviction` |
| Browser (built-in), image served on :8080 | login, dashboard with chart and fonts, Links, the New link dialog (scroll lock) all work; console had one error: the 401 from the first `/auth/me` check on the login page (expected); no CSP violation |
| Trivy, local, same binary and flags as CI (0.76.2, `--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1`) | frontend image and API image (the compose-built `linkforge-api:dev`): 0 findings each, exit code 0 |

NOT measured: image size over time, build time in CI, container memory under the real SPA on kind (until the new image is rolled out).

## P8.4: k6 load test on kind (2026-11-07)

Setup: kind (single node, this 12-core WSL2 machine, k6 shares it), 2 api pods at start (HPA 2 to 6, CPU 70%), 1 worker, Redis and Postgres
in-cluster, Traefik in front. App rate limits raised for the test (redirect 1,000,000/min, create 100,000/min) and **reverted afterwards**.
k6 v2.3.0, `constant-arrival-rate`. Scripts: `infra/k6/`. Server-side latency = the app's own histogram for route `/{code}`, read from Prometheus
over the whole run window (this includes the HPA scale-up at the start of run A). Counts come from k6 and Postgres, not from Prometheus `increase()` (see Findings).

| Run | Where k6 ran | Load | Result |
|---|---|---|---|
| A. warm cache | inside the cluster (Job) | one hot code, 200 req/s for 3 min, started with 2 api pods | 35,987 requests, **100% 302**, 0 x 429, 0 5xx. Client-side p50 5.2 ms, p90 8.9 ms, p95 12.7 ms, max 842 ms. **Server-side p50 3.9 ms, p95 9.6 ms, p99 92.8 ms** (p99 inflated by the scale-up and 4 new pods). k6 could not start 14 of 36,001 iterations (`dropped_iterations`). |
| B. mixed + create | inside the cluster (Job) | 1000 codes (+5% unknown codes, expected 404), 50 req/s, plus 20 creates/min, 3 min, 6 api pods already running | 9,062 iterations, all checks passed (302 for known, 404 for unknown, 201 for creates), 0 x 429, 0 5xx. Client-side redirect_mixed p95 13.9 ms (whole-run p95 43.7 ms includes creates). **Server-side p50 4.8 ms, p95 14.8 ms, p99 23.5 ms.** |
| C. warm cache | this machine (docker, host network, loopback to the kind port) | same as A, but started with 6 api pods | 36,001 requests, **100% 302**, 0 x 429. **Client-side p50 5.2 ms, p90 8.0 ms, p95 9.4 ms, max 92.9 ms.** Server-side p50 3.9 ms, p95 8.8 ms, p99 10.9 ms. |

**Benchmark target** (ARCHITECTURE §7.1, warm redirect p95 server-side < 50 ms): **met**, 9.6 ms (A) and 8.8 ms (C). Honest limits of that statement: one hot
code means a 100% cache hit; a single-node kind cluster on a fast machine is not EKS; the client-side numbers in C exclude any real network.

**Autoscaling (run A, 200 req/s):** in the 15 s samples the HPA showed desired 4 at the second sample (CPU 106%), desired 6 (the maximum) by 62 s and 6 running pods at 77 s, all counted from the first sample (k6 itself started a little later); per-pod API CPU peaked at 0.32 cores (requests are small, so the
HPA reads 160-270% of request). No failed request during the scale-up. Redis working set peaked at 14.7 MiB (alert line 205 MiB). The worker used about 0.4-0.5 CPU cores in both A and B (200/s and 50/s). CORRECTED in P8.5: that is its 500m CPU LIMIT, not a busy loop (73% of its CFS periods were throttled over the drills).

**Click accounting (run A + earlier 15 s smoke):** requests sent about 36,738 (+/- 1), rows stored in Postgres 36,732, `click_events_dropped_total` 6 (2 on each of the 3 api pods the HPA added):
the gap is the dropped count. Rollup sum equals row count (36,732 = 36,732). Stream lag peaked at 17, pending at 23; both back to near 0 at the end. In run C, with 6 warm pods, 0 clicks were dropped.

**Rate limiter, default limits (`limiter.js`, from this machine):** 150 redirects with a different spoofed `X-Forwarded-For` on every request: **exactly 120 got 302, 30 got 429**, so the spoofed header bought nothing
(the limiter keys on the proxy-added address). Creates with the API key: 29 + 1 (setup) = 30 got 201, 11 got 429 (limit 30/min per user). Fixed one-minute windows: one lucky run proves the limiter,
not its edge behaviour.

**Findings (not fixed here):**
1. New api pods lose their first clicks: 2 `cache_get`/`ratelimit` Redis errors and 2 dropped clicks on each pod the HPA added during run A, none when pods were warm (run C). The fallbacks worked (redirects
   were all 302), but clicks were lost. Likely a cold Redis connection on the first requests (NOT proven). 6 clicks of 36,738 (0.016%).
2. `click_stream_length` never shrinks: the worker acknowledges but never deletes entries (XLEN 81,439 with lag 0 and pending 0), so the ARCHITECTURE §7.1 alert `length > 80000` fires in steady state once ~80k
   clicks have ever been processed, not only when there is a backlog. It was firing. RESOLVED in D82: the alert is now on the backlog (lag + pending) and renamed ClickBacklogHigh.
3. `AlertmanagerClusterCrashlooping` (a chart default rule) fired permanently with 0 restarts; process start time appears to jitter under WSL2 (not proven). Disabled in `platform/monitoring/values.yaml`.
4. Prometheus `increase()` over a ~3 minute window mis-states totals (overshoot from extrapolation, undercount for series that appear mid-window): e.g. 9,531 redirects reported for run B where k6 sent at most 9,000.
   Quantiles from histograms are fine; counts are taken from k6 and Postgres.
5. The first commit that "raised" the limits did nothing: the chart's ConfigMap template only rendered fixed keys. Fixed in gitops 772e8aa (optional `RATE_LIMIT_*` keys); the env commit was reverted.

**NOT done / NOT verified:** the "break it" step (raise the rate until errors appear and name the bottleneck); a run with a real network between client and cluster; the scale-DOWN timing (P8.5 CPU drill); the
create scenario at higher rates; any run on EKS. Screenshots of the dashboard during the runs were not taken (P8.5 does that under drills).

## P8.5: failure drills on kind (2026-11-07)

Method: k6 (100 req/s warm redirects, from this machine, client-side) plus a small probe (5 redirects/s and 1 login attempt/s with a different bogus e-mail each time, timeline in `~/runlogs/probe-*.log`)
running during every drill; app rate limits raised for the drills and **reverted** (gitops 4ebbd9a). Click accounting = clicks sent (k6 302s + probe 302s) against rows added to `click_events` in Postgres.
Dashboard evidence: the "LinkForge overview" dashboard for the whole hour was looked at in the built-in browser (lag ramp to about 40k and drain, Redis working set stepping over the 205 MiB line,
HPA at 6, API CPU plateaus); the screenshot was NOT saved to the repo.
Pass criteria are the ones in DEVOPS_GUIDE §13 (targets we chose).

| Drill | What was done | Measured | Pass? |
|---|---|---|---|
| Kill an API pod | `kubectl delete pod <one api pod>` under 100 req/s, started with 2 pods (the load itself made the HPA add pods) | k6: 24,000 requests, 23,998 x 302, 2 failed (0.008%), p95 196 ms, max 2.1 s. Probe in the 80 s after: 180 x 302, 0 errors. A new pod was Ready within 7 s (could be an HPA-added pod, not necessarily the replacement). Clicks: sent 24,501, stored 24,492, 9 not stored (0.04%). | 5xx < 1% yes; replacement Ready < 60 s yes; always at least one pod serving yes |
| Kill Redis | `kubectl delete pod redis-0` under 100 req/s, 6 api pods running | Redis Ready and answering PING in 6 s. k6: 24,000 / 24,000 x 302 (p95 147 ms, max 2.76 s). Probe during the outage (about 8 s): 21 x 302, avg 143 ms against 53 ms normal (Postgres fallback). Login attempts: 2 x 503 (fail closed), 401 otherwise. **Clicks lost: 595 of 24,511 sent (2.4%), equal to the `click_events_dropped_total` increase (595).** AOF: loading 0, last write ok, consumer group kept (consumers 10, pending 6, last-delivered id intact), stream length 100,001 before and 100,015 after (the stream sits at its cap, so this does not show loss). `ClickEventsDropped` fired and reached Alertmanager. | 302 >= 99% yes (100%); dropped rises yes; logins fail closed yes; back < 60 s yes; loss recorded |
| Kill the worker | `worker.replicas: 0` pushed to gitops (Argo would undo `kubectl scale`), 100 req/s for 6.5 min, then reverted by `git revert` | Zero worker pods 32 s after the push. Lag grew about 105 per second (3,106 after 30 s, 39,817 after 6 min), pending 0, stream length stayed at its cap. k6 39,000 / 39,000 x 302. `ClickStreamLagHigh` pending then firing (Alertmanager active since 13:35:05, five minutes after lag passed 5000). After the revert: worker Ready 24 s after the push, **backlog drained 42 s later** (about 39,890 events, roughly 950 per second). Clicks sent 39,895, stored 39,890 (gap 5). | lag grows about rate x downtime yes; lag and pending 0 within 5 min yes (42 s); DB equals sent within the dropped count yes |
| Redis memory pressure | `CONFIG SET maxmemory 8mb` (usage 24 MB) for 60 s under load, then back to 256mb, no restart | Writes refused (`OOM command not allowed when used memory > 'maxmemory'`). Redirects: k6 18,001 / 18,001 x 302, probe 204 / 204 x 302. Logins: 40 x 503 (fail closed). Rate-limiter errors about 6.9k; clicks lost 6,374 of 18,452 sent (a minute at about 105/s, all counted as dropped). Recovery was immediate after the restore: all 21 login probes after it were 401 again and redirect latency was back to normal. The dashboard's 5xx panel peaked at 0.3% (those login 503s) and the cache hit ratio dipped to about 33% while cache writes were refused. | writes fail yes; redirects still 302 yes; logins fail yes; recovers on restore yes |
| AOF rewrite peak memory | 1,000,233 junk keys `drill:*` (100-byte values) added so used_memory was 214.8 MiB, then `BGREWRITEAOF` under 100 req/s of clicks (junk keys removed afterwards, AOF rewritten again to 6.8 MB) | Rewrite took about 7 s, status ok, AOF 53 MB before and 39 MB after. Container `memory.current`: 228-238 MiB before, **peak 240.5 MiB** during the run (about 12 MiB extra); `memory.peak` since container start 249 MiB; cAdvisor working set max 232.5 MiB; limit is 512 Mi. k6 48,000 / 48,000 x 302. `RedisMemoryHigh` (> 205 MiB for 5 min) **fired for real** and reached Alertmanager (active since 13:43:14). Clicks sent 48,977, stored 48,958 (gap 19). | peak below the limit yes (240 of 512 MiB); alert fires yes |
| CPU load, HPA | 600 req/s target for 4 min, started with 2 pods | HPA desired 3 at +28 s (CPU 95% of request), desired 6 (the maximum) at +40 s (487%), 6 pods Ready at +57 s. **Scale-down: back to 2 pods 341 s after the load stopped** (300 s stabilisation window plus polling). The system SATURATED: k6 reached 498 iterations/s (27,826 dropped iterations), client p95 3.3 s, server-side p95 at or above 1 s (the histogram's top bucket is 1 s, so the real value is unknown), 0 5xx, 0 failed k6 requests, 19 of 182 probe redirects timed out at 3 s. | HPA 2 to N within 2-3 min yes (about 1 min), back down after cool-down yes |

**Bottleneck (the "break it" step of P8.4, found here):** the API pods' **500m CPU limit**. At 6 replicas (the HPA maximum) 49% of the API containers' CFS periods were throttled, each pod used up to 0.53 cores, while Redis used 0.25 cores
and Postgres 0.14: neither was the limit. So capacity on this machine is roughly 500 req/s (6 x 500m), and CPU-based HPA cannot go beyond its maximum of 6. k6 shared the host, so the figure is conservative. The click worker has the same
500m limit and was throttled in 73% of its periods across the drills (this is also why its CPU always looked like "0.4-0.5 cores"); it still drained a 39,890-event backlog in 42 s.

**Alerts, honestly:**
- Fired for real and (where noted) reached Alertmanager: `RedisMemoryHigh`, `ClickEventsDropped`, `ClickStreamLagHigh`, and the chart default `KubeHpaMaxedOut`. Slack delivery was NOT tested (D79).
- Went pending but cleared before their `for` elapsed: `LinkForgeRedirectP95High` (the saturating load lasted 4 min, the rule needs 5), `LinkForgeWorkerTargetMissing` (the worker was restored just before 5 min), `LinkForgeTargetDown`.
- NOT exercised at all: `LinkForge5xxRatioHigh` (the only 5xx were login 503s, 0.3% of requests), `ClickBacklogHigh` (backlog peaked at 39.9k of 80k), `LinkForgePodCrashLooping`, `LinkForgeApiTargetMissing`.

**Process notes (so the numbers are trusted):** the first attempt at drill 1 had no load (the k6 script was not on disk after a branch switch: k6 exited at once); the second overlapped with a rolling update that CI started
(every push to the linkforge main branch redeploys, even docs-only pushes; 0.69% failed, max 28.8 s), and was discarded; the third, reported above, is clean. Do not push to linkforge main while a drill runs.

**Already proven in earlier phases (not repeated):** bad image tag, failing migration, drift, Trivy blocking CI, worker idempotency tests (see the P1S/P7 sections).
**NOT done:** worker crash-after-commit under load (covered by tests only); a drain of the node; Postgres down; any of this on EKS; existing sessions surviving a Redis loss (only fresh logins were probed).

