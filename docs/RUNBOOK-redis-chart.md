# Runbook: Redis and the P2 settings reach the kind cluster

Goal: the API image built from P2 needs Redis and five new settings. This phase adds them to the
Helm chart so Argo CD deploys them. After it, **login, sessions, rate limits, the redirect cache and
the host guard work on kind**. All commands run in Ubuntu. You push; Argo CD deploys.

## What changed (read the diff first)

```bash
cd ~/linkforge-gitops && git status --short && git diff
```

| File | What and why |
|---|---|
| `templates/redis.yaml` (new) | A headless Service `redis` and a one-replica StatefulSet with a 1 Gi volume, in **sync wave -1** next to Postgres, so Argo waits for Redis before it starts the API. Flags from ARCHITECTURE §5.1: append-only file on, `maxmemory 256mb`, `noeviction`. Non-root (uid 999), read-only root filesystem, all capabilities dropped. |
| `templates/configmap.yaml` | New settings: `REDIS_URL`, `TRUSTED_PROXY_COUNT`, `ALLOWED_ORIGINS`, `COOKIE_SECURE`, `PUBLIC_REDIRECT_HOST` (taken from `ingress.goHost`, so the host is defined once). |
| `values.yaml` / `envs/kind/values.yaml` | Defaults for Redis; kind says: one proxy (Traefik), allowed origin `http://app.localtest.me`, no Secure cookies (plain HTTP). `TRUSTED_PROXY_COUNT` and `ALLOWED_ORIGINS` are `required`: forgetting them fails the render. |
| `templates/api.yaml` | A `checksum/config` annotation: a hash of the ConfigMap. Pods read a ConfigMap only when they start, so without it a changed setting would sit in the cluster while old pods ignore it. With it, a settings change rolls the pods. |
| `templates/ingress.yaml` | Comment only: the gap from D50 ("the go host forwards every path") is closed by the app, not by the Ingress. |

`IP_HASH_SECRET` is **not** added: the API does not read it until P3 (click events).

## 1. Push order matters: gitops first, then the app

```
1. gitops chart  ->  Argo deploys Redis + settings; the OLD api image keeps running, ignores them
2. linkforge     ->  CI builds the P2 image, bumps the tag, Argo rolls the API
                     and the new API finds Redis waiting
```
The other order gives a window where the P2 API runs with no Redis (every login would be 503).

CI may have added a bump commit to the gitops repo since you last pulled, so pull first:

```bash
cd ~/linkforge-gitops && git pull --rebase && git log --oneline | head -3
```
(The chart change is still uncommitted if you have not committed it: commit it first, e.g.
`git add -A && git commit -m "feat: redis and P2 settings for kind"`, then `git pull --rebase`.)

```bash
git push
```

Skip the 3-minute poll:

```bash
kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite
kubectl -n linkforge get pods -w
```
Expect, in this order: `redis-0` goes `0/1` then `1/1 Running`; then the two `api` pods are
replaced one at a time (the config checksum changed); `postgres-0` and `frontend` untouched.
The app returns to `Synced` + `Healthy`:

```bash
kubectl -n argocd get app linkforge
```

## 2. Check Redis itself

```bash
kubectl -n linkforge get pods,pvc -l app=redis
kubectl -n linkforge exec redis-0 -- redis-cli ping
kubectl -n linkforge exec redis-0 -- redis-cli config get maxmemory-policy
kubectl -n linkforge exec redis-0 -- redis-cli config get appendonly
```
Expect `PONG`, `noeviction`, `yes`, and a `Bound` 1Gi volume named `data-redis-0`.

## 3. Push the application

```bash
cd ~/linkforge && git push
```
This starts `build-push` (about 5 minutes: tests, Trivy scan, push to GHCR, bump commit in the
gitops repo). Watch the Actions tab. **If the Trivy step fails** on one of the new libraries
(argon2-cffi, redis, prometheus_client), nothing is pushed or deployed; paste me the log.
When the bump commit lands, refresh Argo again (command above) and watch the API roll.

## 4. Prove it works end to end

The API's own readiness now reports Redis (port-forward in a second terminal):

```bash
kubectl -n linkforge port-forward svc/api 8000:8000
```
```bash
curl -s localhost:8000/readyz
```
Expect `{"status":"ok","postgres":"ok","redis":"ok"}`.

Register, log in, create a link and follow it, through the real Ingress hosts (this is the browser
flow, so every state-changing request carries the Origin and the CSRF token):

```bash
A=http://app.localtest.me; O='Origin: http://app.localtest.me'; J='content-type: application/json'
curl -s -X POST $A/api/v1/auth/register -H "$J" -H "$O" -d '{"email":"me@example.com","password":"a long enough password"}'
curl -s -c /tmp/kind.jar -X POST $A/api/v1/auth/login -H "$J" -H "$O" -d '{"email":"me@example.com","password":"a long enough password"}'
CSRF=$(awk '$6=="lf_csrf"{print $7}' /tmp/kind.jar)
curl -s -b /tmp/kind.jar -X POST $A/api/v1/links -H "$J" -H "$O" -H "X-CSRF-Token: $CSRF" -d '{"destination_url":"https://example.com/from-kind"}'
```
Expect `201` bodies with an `id`, then a link with a `code`. Use that code (replace `YOURCODE` with
the real `code` value from the last answer):

```bash
curl -si http://go.localtest.me/YOURCODE | head -3
```
Expect `HTTP/1.1 302 Found`.

The **host guard** on the public short-link host:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://go.localtest.me/healthz
curl -s -o /dev/null -w '%{http_code}\n' http://go.localtest.me/api/v1/links
curl -s -o /dev/null -w '%{http_code}\n' http://app.localtest.me/api/v1/links
```
Expect `404`, `404`, and `401` (the app host is not restricted; you are just not logged in on this
curl). The first two are exactly what the old chart could not do.

The **rate limit**, seen through Traefik (one proxy, so the real client address is used):

```bash
for i in $(seq 1 130); do curl -s -o /dev/null -w '%{http_code}\n' http://go.localtest.me/YOURCODE; done | sort | uniq -c
```
Expect about `120 302` then `10 429`. (Wait a minute before repeating; the window is one minute.)

## 5. Break it (drills; record the results in `docs/RESULTS.md`)

**A. Kill Redis.** Redirects must keep working, logins must fail closed, sessions must survive:

```bash
kubectl -n linkforge delete pod redis-0
```
In another terminal, while it restarts (about 10 s):
```bash
for i in $(seq 1 20); do curl -s -o /dev/null -w '%{http_code} ' http://go.localtest.me/YOURCODE; sleep 1; done; echo
curl -s -o /dev/null -w 'login while Redis is down: %{http_code}\n' -X POST http://app.localtest.me/api/v1/auth/login -H 'content-type: application/json' -H 'Origin: http://app.localtest.me' -d '{"email":"me@example.com","password":"a long enough password"}'
```
Expect `302` every time (redirects fail open) and `503` for the login (fail closed). After Redis is
back, your **old cookie still works** because the append-only file on the volume restored the
session:
```bash
curl -s -b /tmp/kind.jar http://app.localtest.me/api/v1/auth/me
```
(If you run this within the first second after the pod restarts you can get a `503`; retry.)

**B. Forget a required setting.** In `~/linkforge-gitops/envs/kind/values.yaml` delete the
`ALLOWED_ORIGINS` line, commit and push, refresh Argo. The app shows a **ComparisonError** that
quotes `config.ALLOWED_ORIGINS is required`, and the cluster is **unchanged** (nothing half
applied). Then `git revert --no-edit HEAD && git push` heals it, no kubectl.

**C. Change a setting and watch the pods roll.** Set `LOG_LEVEL: DEBUG` under `config:` in
`envs/kind/values.yaml`, push, refresh. The API pods are replaced although the image did not change
(the `checksum/config` annotation). Set it back afterwards.

## 6. Record

Resource use with Redis running (idle), for `docs/RESULTS.md`:

```bash
kubectl top pods -n linkforge
kubectl top nodes
```

## Explain it back

1. Why must the gitops chart be pushed before the application?
2. Redis is down: what does a redirect do, what does a login do, and why are they different?
3. What does `noeviction` mean, and what would the app see when Redis is full?
4. Why does the chart carry a hash of the ConfigMap in the API pod template?
5. Why is `TRUSTED_PROXY_COUNT` a `required` value with no default?
6. What makes the go host answer 404 to `/api/v1/links`: the Ingress or the app? Where is the host
   name defined, and how many places must change to rename it?
