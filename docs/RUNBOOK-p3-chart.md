# Runbook: the click worker and the P3 settings reach the kind cluster

Goal: the API image built from P3 needs one new secret (`IP_HASH_SECRET`) and comes with a second
process, the click worker. This phase adds both to the Helm chart. After it, **every redirect on
kind queues a click, the worker stores it, and the analytics endpoints show it**. All commands run
in Ubuntu. You run the cluster commands and push; Argo CD deploys.

## What changed (read the diff first)

```bash
cd ~/linkforge-gitops && git status --short && git diff
```

| File | What and why |
|---|---|
| `templates/worker.yaml` (new) | A Deployment `worker`, one replica, **the same image as the API** (it follows `api.image.tag`) with the command `python -m app.worker`, plus a Service `worker` whose only port is `metrics` (9100). Same hardening as the API (non-root, read-only filesystem, no capabilities). No readiness probe (it serves no traffic); liveness asks its metrics endpoint. Sync wave 1, after Postgres, Redis and the migration. |
| `templates/api.yaml` | Env `IP_HASH_SECRET` from the Secret; container port `metrics` (9101); the `api` Service gains a `metrics` port. The Ingresses still name the port `http`, so metrics are never routed from outside. |
| `templates/_helpers.tpl` | The helper that reads `IP_HASH_SECRET` from the Secret (never from values, never from git). |
| `values.yaml` | A `worker:` block (resources, `enabled`). |
| `envs/kind/values.yaml` | `worker.enabled: false` **for now**: see the next section. |

## The one dangerous thing: order

Three facts decide the order:

1. The new API **refuses to start** without `IP_HASH_SECRET`, and the chart now injects it from the
   Secret. If the Secret lacks the key, the pod cannot even be created (`CreateContainerConfigError`).
2. The P2 image that is running now has **no `app.worker` module**. A worker started from it would
   crash-loop.
3. CI replaces the image tag by itself when you push the application.

So: **secret first, chart second (worker still off), application third, worker last.** At every
step the old pods keep serving (`maxUnavailable: 0`), and at no step is anything started from an
image that cannot run it. `worker.enabled: false` is a feature flag: the chart ships the code path
early and you switch it on once the image underneath is ready (the same idea as the database
expand/contract rule).

## 1. Create the secret key (by hand, never in git, never pasted anywhere)

The value is generated in YOUR terminal and goes straight into the cluster; I never see it.

```bash
kubectl -n linkforge patch secret linkforge-db --type merge -p "{\"stringData\":{\"IP_HASH_SECRET\":\"$(openssl rand -hex 32)\"}}"
```
Expect `secret/linkforge-db patched`. Check that the key exists (this prints key NAMES, not values):

```bash
kubectl -n linkforge get secret linkforge-db -o jsonpath='{.data}' | python3 -c 'import sys,json; print(sorted(json.load(sys.stdin)))'
kubectl -n linkforge get secret linkforge-db -o jsonpath='{.data.IP_HASH_SECRET}' | base64 -d | wc -c
```
Expect `['DATABASE_URL', 'IP_HASH_SECRET', 'POSTGRES_PASSWORD']` and `64`.

A Secret is only base64, not encrypted (P7 replaces this by-hand step with Sealed Secrets).
Do not run this command again later: a NEW value would change the hash of every visitor address,
so unique-visitor continuity would break.

## 2. Push the chart (the worker is still off)

```bash
cd ~/linkforge-gitops && git pull --rebase
git add -A && git commit -m "feat: worker, IP_HASH_SECRET and metrics ports in the chart" && git push
kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite
kubectl -n linkforge get pods -w
```
Expect: the two `api` pods are replaced one at a time (their pod template changed: new env and
port), still running the P2 image; **no worker pod** (it is off). The app returns to
`Synced` / `Healthy`:

```bash
kubectl -n argocd get app linkforge
kubectl -n linkforge exec deploy/api -- sh -c 'test -n "$IP_HASH_SECRET" && echo IP_HASH_SECRET is set'
kubectl -n linkforge get svc api -o jsonpath='{.spec.ports[*].name}'; echo
```
Expect `IP_HASH_SECRET is set` and `http metrics`.

## 3. Push the application

```bash
cd ~/linkforge && git push
```
CI (about 5 minutes) builds the P3 image, scans it, pushes it and adds a bump commit to the gitops
repo; Argo then rolls the API to it. Watch the Actions tab. **If the Trivy step fails** on the new
library (`ua-parser`), nothing is deployed: paste me the log. After the bump:

```bash
cd ~/linkforge-gitops && git pull --rebase
kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite
kubectl -n linkforge get pods -w
```
Expect two new `api-...` pods, `1/1 Running`, and the migrate Job re-run (as with every sync).
Then look at the API's metrics through a port-forward (second terminal):

```bash
kubectl -n linkforge port-forward svc/api 9101:9101
```
```bash
curl -s localhost:9101/metrics | grep -E '^(http_requests_total|click_stream_)' | head
```
Expect `click_stream_length`, `click_stream_lag` and `click_stream_pending` (all `0.0` for a fresh
stream) and some `http_requests_total{...}` lines with the route `/api/v1/auth/...` or `/readyz`.

## 4. Switch the worker on

In `~/linkforge-gitops/envs/kind/values.yaml` change

```
worker:
  enabled: false
```
to `enabled: true`, then:

```bash
cd ~/linkforge-gitops
git commit -am "feat: enable the click worker on kind" && git push
kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite
kubectl -n linkforge get pods -w
```
Expect one `worker-...` pod: `0/1` then `1/1 Running`. Check it:

```bash
kubectl -n linkforge logs deploy/worker --tail 5
```
Expect JSON lines: `metrics server listening on port 9100` and `click worker worker-... starting`.

## 5. Prove it end to end

Log in (the account from the Redis runbook), create a link and click it a few times, each with a
different browser string, then ask the analytics endpoint:

```bash
A=http://app.localtest.me; O='Origin: http://app.localtest.me'; J='content-type: application/json'
curl -s -c /tmp/kind.jar -X POST $A/api/v1/auth/login -H "$J" -H "$O" -d '{"email":"me@example.com","password":"a long enough password"}' >/dev/null
CSRF=$(awk '$6=="lf_csrf"{print $7}' /tmp/kind.jar)
curl -s -b /tmp/kind.jar -X POST $A/api/v1/links -H "$J" -H "$O" -H "X-CSRF-Token: $CSRF" -d '{"destination_url":"https://example.com/counted"}' | tee /tmp/kind-link2.json
CODE=$(python3 -c 'import json;print(json.load(open("/tmp/kind-link2.json"))["code"])'); LID=$(python3 -c 'import json;print(json.load(open("/tmp/kind-link2.json"))["id"])'); echo; echo "code=$CODE"
```
```bash
for i in 1 2 3; do curl -s -o /dev/null -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36" -e "https://news.example.com/story?token=SECRET" http://go.localtest.me/$CODE; done
curl -s -o /dev/null -A "curl/8.5.0" http://go.localtest.me/$CODE
sleep 6
curl -s -b /tmp/kind.jar "$A/api/v1/links/$LID/analytics?range=7d" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("total", d["total"], "| referrers", d["referrers"], "| devices", d["devices"], "| browsers", d["browsers"])'
```
Expect `total 3` (the curl click is a bot: stored, not counted), referrer `news.example.com` (host
only: the `token=SECRET` part is gone), device `desktop`, browser `Chrome`.

The same numbers from the worker's side (second terminal, port-forward to the worker):

```bash
kubectl -n linkforge port-forward svc/worker 9100:9100
```
```bash
curl -s localhost:9100/metrics | grep -E '^(click_events_processed_total|click_stream_)'
```
Expect `click_events_processed_total{status="ok"}` of at least 4 and `click_stream_lag 0.0`.

**Metrics are not public.** None of these may print Prometheus text:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://go.localtest.me/metrics
curl -s -o /dev/null -w '%{http_code}\n' http://app.localtest.me/api/metrics
curl -s http://app.localtest.me/metrics | head -c 80; echo
```
Expect `404`, `404`, and the placeholder HTML page (the app host sends everything outside `/api` to
the frontend, which has no `/metrics`).

## 6. Break it (drills; record the results in `docs/RESULTS.md`)

**A. Kill the worker; lag must grow, then drain.**

```bash
kubectl -n linkforge scale deploy/worker --replicas=0
```
Argo's self-heal will put it back to 1 within moments: that is itself the lesson. To watch lag
grow you need the worker gone for longer, so turn it off the GitOps way: set `enabled: false`
again in `envs/kind/values.yaml`, push, refresh, wait for the worker pod to disappear, click the
link 10 times, and read the gauge on the API port (`curl -s localhost:9101/metrics | grep
click_stream_lag`): expect about `10.0`. Then set `enabled: true`, push, refresh: the lag drains to
`0.0` within seconds and the analytics total grows by 10.

**B. A message that can never be processed.**

```bash
kubectl -n linkforge exec redis-0 -- redis-cli XADD clicks '*' event_id not-a-uuid link_id nope
sleep 3
kubectl -n linkforge exec redis-0 -- redis-cli XRANGE clicks:dead - +
```
Expect one entry with `original_id`, `error malformed: ...` and `failed_at`, and
`click_events_processed_total{status="dead"}` going up by one on the worker's metrics.
(Clean up if you like: `kubectl -n linkforge exec redis-0 -- redis-cli DEL clicks:dead`.)

**C. Stop Redis; the worker survives.**

```bash
kubectl -n linkforge delete pod redis-0
kubectl -n linkforge get pods -w
```
Expect `worker` to stay `Running` with at most a log line about Redis; after Redis returns, new
clicks are stored again.

## 7. Record

```bash
kubectl top pods -n linkforge
kubectl top nodes
```
Add the numbers to the "Resource use" section of `docs/RESULTS.md`.

## Explain it back

1. Why do the secret, the chart, the application and the worker have to go in that order, and what
   would each wrong order have done?
2. The worker uses the same image as the API. Which two things in the chart make it a worker?
3. What does `worker.enabled: false` buy you, and why is that safer than deleting the file and
   adding it back later?
4. Why is a Secret not encrypted, and what will replace this by-hand step in P7?
5. Why must the metrics port never appear in an Ingress, and where in the chart is that guaranteed?
6. The stream gauges come from the API as well as the worker. Why, and what must an alert query
   use because of it?
