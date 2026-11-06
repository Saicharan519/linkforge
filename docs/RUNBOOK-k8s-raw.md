# Runbook: deploy LinkForge to kind by hand (raw manifests, P1S.4b)

You apply every object yourself so you see what each one does. The files are in
`infra/k8s-raw/`, heavily commented; read each one before applying it. All commands run in your
Ubuntu terminal from `~/linkforge`. Reference: `docs/DEVOPS_GUIDE.md` §7.

## 0. Cluster up (it was deleted at the end of 4a)

Same steps as `docs/RUNBOOK-kind.md` §2 to §4, condensed:

```bash
kind create cluster --config infra/kind/kind-config.yaml
```
```bash
helm install traefik traefik/traefik --version 42.2.0 --namespace traefik --create-namespace -f infra/kind/traefik-values.yaml
```
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.1/components.yaml
```
```bash
kubectl -n kube-system patch deployment metrics-server --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```
```bash
kubectl -n traefik rollout status deploy/traefik && kubectl -n kube-system rollout status deploy/metrics-server
```

## 1. Namespace and config

```bash
kubectl apply -f infra/k8s-raw/00-namespace-config.yaml
kubectl -n linkforge get configmap linkforge-config -o yaml | head -20
```

## 2. The database secret (by hand, never in git)

```bash
PW=$(openssl rand -hex 16)
kubectl -n linkforge create secret generic linkforge-db \
  --from-literal=POSTGRES_PASSWORD="$PW" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://linkforge:${PW}@postgres:5432/linkforge"
unset PW
```

`openssl rand -hex` produces only 0-9a-f, so the password needs no URL-escaping inside
`DATABASE_URL`. The host name `postgres` is the headless Service you apply next.

```bash
kubectl -n linkforge get secret linkforge-db          # expect DATA = 2
kubectl -n linkforge get secret linkforge-db -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d | wc -c   # expect 32
```

Note what that last command shows: a Secret is only **base64-encoded**, not encrypted. Anyone
with `kubectl get secret` access can read it. That is exactly why P7 replaces this by-hand step
with Sealed Secrets (encrypted values that are safe to keep in git).

## 3. PostgreSQL

```bash
kubectl apply -f infra/k8s-raw/10-postgres.yaml
kubectl -n linkforge get pods -w          # Ctrl-C once postgres-0 shows 1/1 Running
```
```bash
kubectl -n linkforge rollout status statefulset/postgres
kubectl -n linkforge get pvc              # expect data-postgres-0   Bound   1Gi
kubectl -n linkforge exec postgres-0 -- pg_isready -U linkforge -d linkforge   # expect: accepting connections
```

## 4. Migration

```bash
kubectl apply -f infra/k8s-raw/20-migrate-job.yaml
kubectl -n linkforge wait --for=condition=complete job/migrate --timeout=180s
kubectl -n linkforge logs job/migrate     # expect: Running upgrade  -> 0001, initial schema ...
```

The first run pulls the image from GHCR, so allow a minute. If it shows `Error` pods first and
then succeeds, that is the Job retrying while Postgres was still starting.

## 5. API and frontend

```bash
kubectl apply -f infra/k8s-raw/30-api.yaml -f infra/k8s-raw/40-frontend.yaml
kubectl -n linkforge rollout status deploy/api deploy/frontend
kubectl -n linkforge get pods -o wide
kubectl -n linkforge get endpoints api    # the Ready pod IPs behind the Service
```

Expected: 2 api pods and 1 frontend pod, all `1/1 Running`.

## 6. Ingress and the first request

```bash
kubectl apply -f infra/k8s-raw/50-ingress.yaml
kubectl -n linkforge get ingress
```
```bash
curl -s http://app.localtest.me/ | grep -o '<h1>.*</h1>'         # expect: <h1>LinkForge</h1>
```
```bash
curl -s -H 'Content-Type: application/json' -d '{"destination_url":"https://example.com","alias":"Kube01"}' http://app.localtest.me/api/v1/links
```
```bash
curl -si http://go.localtest.me/Kube01 | head -4                 # expect: HTTP/1.1 302 Found + a location header
```

If `app.localtest.me` does not resolve (some routers block names that point at 127.0.0.1),
either add `127.0.0.1 app.localtest.me go.localtest.me` to `/etc/hosts` with
`echo "127.0.0.1 app.localtest.me go.localtest.me" | sudo tee -a /etc/hosts`, or add
`--resolve app.localtest.me:80:127.0.0.1 --resolve go.localtest.me:80:127.0.0.1` to each curl.

You can also open `http://app.localtest.me` in your Windows browser.

Look at real resource use and compare it with the requests we guessed in ARCHITECTURE §9.3:

```bash
kubectl top pods -n linkforge
```

## 7. Break it (do these in order, one at a time)

Keep a second terminal running this loop for drills A to C. It prints the status code twice a
second; a healthy system prints `302` all the time:

```bash
while true; do curl -s -o /dev/null -w '%{http_code} ' http://go.localtest.me/Kube01; sleep 0.5; done
```

**A. Self-healing.** Delete one API pod:

```bash
kubectl -n linkforge get pods -l app=api
kubectl -n linkforge delete pod <one-of-the-api-pod-names>
kubectl -n linkforge get pods -l app=api -w
```

Expect: a replacement appears within seconds, and the loop never stops printing 302 because the
other replica kept serving.

**B. State survives a pod, because the disk is separate.** Delete the database pod:

```bash
kubectl -n linkforge delete pod postgres-0
kubectl -n linkforge rollout status statefulset/postgres
curl -si http://go.localtest.me/Kube01 | head -1       # still 302: the link is still in the database
```

**C. A bad release does not take you down.** Point the API at an image tag that does not exist:

```bash
kubectl -n linkforge set image deploy/api api=ghcr.io/saicharan519/linkforge-api:sha-doesnotexist
kubectl -n linkforge get pods -l app=api
```

Expect: a NEW pod stuck in `ErrImagePull` / `ImagePullBackOff`, while both OLD pods stay
`Running`, and the loop keeps printing 302. That is `maxUnavailable: 0` in action: old pods are
only removed after a new one is Ready, and the new one never becomes Ready.

```bash
kubectl -n linkforge describe pod -l app=api | grep -A3 -i "failed\|back-off" | head
kubectl -n linkforge rollout undo deploy/api
kubectl -n linkforge rollout status deploy/api
```

`rollout undo` is the by-hand cousin of what you will do with `git revert` once Argo CD owns
this (P1S.5).

**D. Dependency down: readiness, not restarts.** Stop the database:

```bash
kubectl -n linkforge scale statefulset postgres --replicas=0
kubectl -n linkforge get pods -l app=api -w
```

Expect: the api pods go to `0/1` (not Ready, because `/readyz` checks Postgres), the loop starts
printing `503`, and the `RESTARTS` column stays at `0`. Liveness uses `/healthz`, which does not
check Postgres, so Kubernetes does not restart-loop pods for a problem a restart cannot fix.

```bash
kubectl -n linkforge scale statefulset postgres --replicas=1
kubectl -n linkforge rollout status statefulset/postgres
```

The pods turn Ready again by themselves and the loop returns to `302`.

**E. Memory limit: OOMKilled.** Give the API an impossible limit:

```bash
kubectl -n linkforge set resources deploy/api --requests=memory=10Mi --limits=memory=20Mi
kubectl -n linkforge get pods -l app=api -w
kubectl -n linkforge describe pod -l app=api | grep -B2 -A6 "OOMKilled" | head -20
```

Expect: new pods `OOMKilled` then `CrashLoopBackOff` (exit code 137), old pods keep serving.
Undo it:

```bash
kubectl -n linkforge rollout undo deploy/api
kubectl -n linkforge rollout status deploy/api
```

## 8. Clean up before the Helm chart (P1S.4c)

The Helm chart reuses the same object names, so the hand-applied copies must go first:

```bash
kubectl delete namespace linkforge
```

(Deleting the namespace deletes everything inside it, including the database volume.) Leave the
cluster, Traefik and metrics-server running.

## Explain it back

1. What does a Deployment do that a bare Pod does not? What is the ReplicaSet for?
2. Why is Postgres a StatefulSet with its own volume claim?
3. Service vs Ingress: what does each one route, and to what?
4. Readiness vs liveness: what happens when each one fails, and why does drill D matter?
5. What is the difference between a request and a limit, and what did drill E show?
6. Why does `maxUnavailable: 0` make a bad release safe?
7. Why is base64 in a Secret not security, and what replaces it later?
