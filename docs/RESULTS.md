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

