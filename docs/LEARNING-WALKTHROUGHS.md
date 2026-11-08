# Learning walkthroughs (the one-time hands-on exercises on kind)

Five phase walkthroughs, merged on 2026-11-09 from `RUNBOOK-k8s-raw.md`, `RUNBOOK-helm.md`, `RUNBOOK-argocd.md`, `RUNBOOK-redis-chart.md` and
`RUNBOOK-p3-chart.md` (nothing was dropped; each keeps its own "Explain it back" and "Break it" parts). They are kept because `DECISIONS.md`, `RESULTS.md`
and `TASKS.md` cite them as evidence. They describe how the project was learned, step by step, in phase order. For CURRENT operation use
`RUNBOOK-kind-cluster.md` (local cluster), `RUNBOOK-monitoring.md` and `RUNBOOK-eks-run-day.md` (AWS). Some commands here were later automated by
`scripts/bootstrap-kind.sh`.

| # | Phase | Section |
|---|---|---|
| 1 | P1S.4b | Deploy to kind by hand with raw manifests |
| 2 | P1S.4c | The same deployment as a Helm chart |
| 3 | P1S.5 | Argo CD takes over |
| 4 | P2 | Redis and the P2 settings reach the cluster |
| 5 | P3 | The click worker and the P3 settings reach the cluster |

## 1. Runbook: deploy LinkForge to kind by hand (raw manifests, P1S.4b)

You apply every object yourself so you see what each one does. The files are in
`infra/k8s-raw/`, heavily commented; read each one before applying it. All commands run in your
Ubuntu terminal from `~/linkforge`. Reference: `docs/DEVOPS_GUIDE.md` §7.

### 0. Cluster up (it was deleted at the end of 4a)

Same steps as `docs/RUNBOOK-kind-cluster.md` (Part 2, sections 2 to 4), condensed:

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

### 1. Namespace and config

```bash
kubectl apply -f infra/k8s-raw/00-namespace-config.yaml
kubectl -n linkforge get configmap linkforge-config -o yaml | head -20
```

### 2. The database secret (by hand, never in git)

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

### 3. PostgreSQL

```bash
kubectl apply -f infra/k8s-raw/10-postgres.yaml
kubectl -n linkforge get pods -w          # Ctrl-C once postgres-0 shows 1/1 Running
```
```bash
kubectl -n linkforge rollout status statefulset/postgres
kubectl -n linkforge get pvc              # expect data-postgres-0   Bound   1Gi
kubectl -n linkforge exec postgres-0 -- pg_isready -U linkforge -d linkforge   # expect: accepting connections
```

### 4. Migration

```bash
kubectl apply -f infra/k8s-raw/20-migrate-job.yaml
kubectl -n linkforge wait --for=condition=complete job/migrate --timeout=180s
kubectl -n linkforge logs job/migrate     # expect: Running upgrade  -> 0001, initial schema ...
```

The first run pulls the image from GHCR, so allow a minute. If it shows `Error` pods first and
then succeeds, that is the Job retrying while Postgres was still starting.

### 5. API and frontend

```bash
kubectl apply -f infra/k8s-raw/30-api.yaml -f infra/k8s-raw/40-frontend.yaml
kubectl -n linkforge rollout status deploy/api deploy/frontend
kubectl -n linkforge get pods -o wide
kubectl -n linkforge get endpoints api    # the Ready pod IPs behind the Service
```

Expected: 2 api pods and 1 frontend pod, all `1/1 Running`.

### 6. Ingress and the first request

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

### 7. Break it (do these in order, one at a time)

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

### 8. Clean up before the Helm chart (P1S.4c)

The Helm chart reuses the same object names, so the hand-applied copies must go first:

```bash
kubectl delete namespace linkforge
```

(Deleting the namespace deletes everything inside it, including the database volume.) Leave the
cluster, Traefik and metrics-server running.

### Explain it back

1. What does a Deployment do that a bare Pod does not? What is the ReplicaSet for?
2. Why is Postgres a StatefulSet with its own volume claim?
3. Service vs Ingress: what does each one route, and to what?
4. Readiness vs liveness: what happens when each one fails, and why does drill D matter?
5. What is the difference between a request and a limit, and what did drill E show?
6. Why does `maxUnavailable: 0` make a bad release safe?
7. Why is base64 in a Secret not security, and what replaces it later?

## 2. Runbook: the same deployment, as a Helm chart (P1S.4c)

The chart lives in the **other** repo, `~/linkforge-gitops` (`charts/linkforge/`), and the kind
settings in `envs/kind/values.yaml`. Read the templates first; each carries comments on why.
Reference: `docs/DEVOPS_GUIDE.md` §8. Run everything in Ubuntu.

### What Helm is, in one paragraph

A chart is a folder of **templates** (YAML with `{{ }}` holes) plus default **values**. Helm
fills the holes and applies the result. `helm template` only renders (changes nothing), which is
how you learn what a chart does. `helm install` renders and applies, and remembers the result as a
**release** with a numbered history, so `helm rollback` works. Environment differences live only
in the values file you pass with `-f`; the templates never change between kind and EKS.

### 1. Look before you apply

```bash
cd ~/linkforge-gitops
helm lint charts/linkforge -f envs/kind/values.yaml
```
Expect: `1 chart(s) linted, 0 chart(s) failed` (an "icon is recommended" note is fine).

```bash
helm template linkforge charts/linkforge -n linkforge -f envs/kind/values.yaml | less
```
Scroll through it: this is plain Kubernetes YAML, close to the files you applied by hand in
4b. Find where `tag:` from the values file ended up, and the `argocd.argoproj.io/...`
annotations (Helm ignores them; Argo CD uses them in P1S.5).

### 2. Prepare the namespace and secret

The cluster from 4b is still running; the `linkforge` namespace was deleted at the end of that
runbook, and the secret went with it. The chart deliberately does not create the secret.

```bash
kubectl create namespace linkforge
PW=$(openssl rand -hex 16)
kubectl -n linkforge create secret generic linkforge-db \
  --from-literal=POSTGRES_PASSWORD="$PW" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://linkforge:${PW}@postgres:5432/linkforge"
unset PW
```

### 3. Install

```bash
helm install linkforge charts/linkforge -n linkforge -f envs/kind/values.yaml --wait --timeout 5m
```
`--wait` returns only when the Deployments are Ready. Then:

```bash
helm list -n linkforge                      # one release, STATUS deployed, REVISION 1
kubectl -n linkforge get pods               # postgres-0, 2x api, frontend, migrate (Completed)
kubectl -n linkforge logs job/migrate       # Running upgrade  -> 0001 ...
```
```bash
curl -s -H 'Content-Type: application/json' -d '{"destination_url":"https://example.com","alias":"Helm01"}' http://app.localtest.me/api/v1/links
curl -si http://go.localtest.me/Helm01 | head -4      # 302 again, same as 4b
```

If that works, the chart reproduces what you built by hand. That is the point of 4b and 4c.

### 4. Break it

**A. Change a value, watch a release.** Helm keeps history:
```bash
helm upgrade linkforge charts/linkforge -n linkforge -f envs/kind/values.yaml --set api.replicas=3
kubectl -n linkforge get pods -l app=api        # 3 pods
helm history linkforge -n linkforge              # revision 2
helm rollback linkforge 1 -n linkforge           # back to 2 pods
kubectl -n linkforge get pods -l app=api -w
```

**B. A typo in a values key is silent.** Helm does not know which keys are valid:
```bash
helm template x charts/linkforge -n linkforge -f envs/kind/values.yaml --set api.replica=9 | grep replicas
```
The output still says `replicas: 2`: your `api.replica` (missing "s") was ignored without any
error. That is why `required` exists for the values that MUST be set, and why you always read the
rendered output.

**C. A forgotten required value fails loudly:**
```bash
helm template x charts/linkforge -n linkforge -f envs/kind/values.yaml --set ingress.className=
```
Expect an error saying `ingress.className is required (traefik|alb)`.

**D. Render for another environment and diff.** Same templates, only the class changes:
```bash
diff <(helm template x charts/linkforge -n linkforge -f envs/kind/values.yaml) \
     <(helm template x charts/linkforge -n linkforge -f envs/kind/values.yaml --set ingress.className=alb)
```

**E. Why the Argo hook-delete annotation exists.** Change the image tag with plain Helm:
```bash
helm upgrade linkforge charts/linkforge -n linkforge -f envs/kind/values.yaml --set api.image.tag=sha-0000000
```
Expect the upgrade to FAIL with an error about the Job `migrate` and an **immutable** field. A
Job's pod template cannot be changed once created, and Helm does not know it should delete and
recreate it. Argo CD does, thanks to `hook-delete-policy: BeforeHookCreation` on the Job; that is
why every new image tag will work under Argo in P1S.5.

Then look at what the half-finished upgrade left behind. This part is my prediction, not something
I have run, so tell me if it differs: Helm applies Deployments BEFORE Jobs, so the API Deployment
probably already points at the nonexistent tag.

```bash
helm history linkforge -n linkforge        # the last revision should say "failed"
kubectl -n linkforge get pods -l app=api   # probably 2 old Running + 1 new in ImagePullBackOff
```

If so, you are looking at drill C of 4b again (old pods keep serving), now caused by a failed
release. Fix it by rolling back to the last revision that worked (the number in `helm history`
whose status is `deployed`/`superseded`):

```bash
helm rollback linkforge <that-revision> -n linkforge
```

### 5. Hand over to Argo CD (do this right before P1S.5, not now)

Two controllers managing the same objects fight each other, so once Argo owns the app, Helm must
let go. Removing the release deletes the objects it created, but **not** the namespace, the secret
or the database volume (Helm never owned those), so the database password stays valid:

```bash
helm uninstall linkforge -n linkforge
```

### Explain it back

1. What is the difference between a chart, a release, and a values file?
2. `helm template` vs `helm install`: what does each change?
3. Why does the chart not contain the Secret, and what will contain it later?
4. What did drill B show about Helm and typos, and what protects you?
5. Why did drill E fail under plain Helm, and what makes it work under Argo CD?
6. Where does the image tag come from, and who changes it?

## 3. Runbook: Argo CD takes over (P1S.5)

> **Update (P7D, D78):** the manual steps in this runbook (installing Argo CD, applying `linkforge-app.yaml`) are now
> done by `scripts/bootstrap-kind.sh`, and the Application lives in `argocd/apps/linkforge.yaml` under `argocd/root.yaml`.
> See docs/RUNBOOK-kind-cluster.md (Part 1). This file stays as the explanation of what each step does.

Goal: stop deploying by hand. After this phase **a git push is a deploy and `git revert` is a
rollback**. Read `docs/DEVOPS_GUIDE.md` §9 first if you have not. All commands run in Ubuntu.

### The idea in one picture

```
you / CI  --push-->  linkforge-gitops (git)  <--polls every ~3 min--  Argo CD (in the cluster)
                      charts/ + envs/kind/values.yaml                     |  renders the chart
                                                                          v  and makes the cluster match
                                                                     namespace "linkforge"
```

Argo CD runs **inside** the cluster and pulls from git. Nothing from outside needs cluster
credentials, which is exactly why CI never runs `kubectl`. Two behaviours do the real work:
**sync** (apply what git says) and **self-heal** (undo anything someone changes by hand).

### 0. Preparation

Pull the commit CI added to the gitops repo for your last push (the image tag bump), and confirm
the earlier handover left what we need:

```bash
cd ~/linkforge-gitops && git pull --rebase && git log --oneline | head -4
```
Expect a `linkforge-ci` commit named `chore: deploy sha-xxxxxxx to kind` on top. If it is not there
yet, the CI run for your push is still going (about 5 min); look in the Actions tab.

```bash
kubectl -n linkforge get all,pvc,secret 2>&1 | head -20
```
Expect NO pods (Helm removed them), but the `linkforge-db` secret and the `data-postgres-0`
volume claim still exist. Argo will reuse both, so your database contents (the `Helm01` link)
survive the handover.

Apply the Traefik change from this phase (one value, explained in D53). Without it Argo would
show the app as "Progressing" forever because kind has no load balancer to give the Ingress an address:

```bash
cd ~/linkforge && helm upgrade traefik traefik/traefik --version 42.2.0 -n traefik -f infra/kind/traefik-values.yaml
```
```bash
kubectl -n traefik rollout status deploy/traefik
```

### 1. Install Argo CD

Pinned to **v3.6.1**, by the exact git COMMIT of that release (a tag can be moved, a commit
cannot), and checked against a hash before it touches the cluster. The hash below is the one I
computed from two separate downloads; it proves the file did not change in transit, not that
upstream is trustworthy: that trust comes from pinning the version and reading what is in it.

```bash
cd /tmp && curl -fLo argocd-install.yaml https://raw.githubusercontent.com/argoproj/argo-cd/94037c4d4e1af4bd0651d37a179fbd9e347e668c/manifests/install.yaml
```
```bash
echo "7be23ca7d9ca3d3074dee8332f1ca92a933b990cee7c9cf22cf3843ee8c2913e  argocd-install.yaml" | sha256sum --check
```
Expect `argocd-install.yaml: OK`. If it says FAILED, stop and tell me.

```bash
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts -f /tmp/argocd-install.yaml
```
`--server-side` is required here: Argo's CRDs are bigger than the annotation limit of the normal
client-side apply and would be rejected.

```bash
kubectl -n argocd rollout status deploy/argocd-server deploy/argocd-repo-server deploy/argocd-redis
kubectl -n argocd rollout status statefulset/argocd-application-controller
```

Save memory by switching off three components this project does not use (single sign-on, chat
notifications, ApplicationSets). This is a local-only tweak:

```bash
kubectl -n argocd scale deploy argocd-dex-server argocd-notifications-controller argocd-applicationset-controller --replicas=0
kubectl -n argocd get pods
```
Expect 4 pods Running: server, repo-server, redis, application-controller.

### 2. Open the UI

Leave this running in a second terminal tab:

```bash
kubectl -n argocd port-forward svc/argocd-server 8443:443
```

Open **https://localhost:8443** in your Windows browser and accept the self-signed certificate
warning (the traffic never leaves your machine). Log in as `admin`. Print the one-time password
in YOUR terminal (it is a credential: do not paste it anywhere, including to me):

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

### 3. Hand the app to Argo

```bash
cd ~/linkforge-gitops && kubectl apply -f argocd/linkforge-app.yaml
```
Now watch, in the UI (click the `linkforge` tile) and in the terminal:

```bash
kubectl -n argocd get applications.argoproj.io -w
kubectl -n linkforge get pods -w
```

What you should see, in this order (these are the **sync waves**):
1. the ConfigMap (wave -2),
2. `postgres-0` starts and must become Ready (wave -1),
3. the `migrate` Job runs and completes (wave 0, an Argo "Sync hook"),
4. only then the API, frontend, Services and Ingresses (wave 1).

Compare with the plain `helm install` in 4c: there the migrate Job started at the same moment as
Postgres and failed once ("Error" pod). **Here there should be no failed migrate pod.** That is
the whole point of the waves. The application ends as `Synced` + `Healthy`.

```bash
curl -si http://go.localtest.me/Helm01 | head -3
```
Expect `302`: your old link, still in the database.

### 4. The three proofs (the "Done when" of the walking skeleton)

Record each in `docs/RESULTS.md` (a table is there): what you did, what you saw, how long it took.
This is portfolio evidence.

#### Proof 1: a code change deploys itself

Change something you can see. In `~/linkforge`:

```bash
sed -i 's/Placeholder page\./Placeholder page, deployed by Argo CD./' frontend/public/index.html
git add -A && git commit -m "chore: visible change for the GitOps proof" && git push
```

Watch the chain: GitHub Actions (about 5 min) → a new `linkforge-ci` commit in the gitops repo →
Argo notices (it polls about every 3 minutes; to skip the wait click **Refresh** on the app, or
run `kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite`) →
rolling update. Then reload `http://app.localtest.me`: the new text appears. You also see the
migrate Job being deleted and recreated (`BeforeHookCreation`), which is what plain Helm could
not do in drill E of 4c.

#### Proof 2: self-heal (git is the only way to change things)

```bash
kubectl -n linkforge scale deploy/api --replicas=5
kubectl -n linkforge get pods -l app=api -w
```
Expect: extra pods appear for a moment, then Argo puts it back to 2 within seconds (the UI shows
the app briefly `OutOfSync`). Then try deleting a whole object:

```bash
kubectl -n linkforge delete deploy/frontend
```
Argo recreates it.

#### Proof 3: a bad release does not take you down, and `git revert` heals

Break the FRONTEND tag in the gitops repo (a nonexistent image):

```bash
cd ~/linkforge-gitops && git pull --rebase
sed -i -E 's|^([[:space:]]*tag: )"[^"]*"([[:space:]]*# ci:frontend-tag)|\1"sha-0000000"\2|' envs/kind/values.yaml
git diff --stat
git commit -am "break: nonexistent frontend tag (GitOps proof 3)" && git push
```

Wait for Argo to sync (or Refresh), then:

```bash
kubectl -n linkforge get pods -l app=frontend
curl -s http://app.localtest.me/ | grep -o '<h1>.*</h1>'
```
Expect: one NEW frontend pod in `ImagePullBackOff`, the OLD pod still `Running` and the page
still served; the app shows `Progressing` or `Degraded` in Argo. (Same `maxUnavailable: 0` safety
net as drill C in 4b.) Now heal it the GitOps way:

```bash
git revert --no-edit HEAD && git push
```
Argo syncs the reverted file, the bad pod disappears, and the app is `Healthy` again. **No kubectl
was needed to fix it.**

### 5. Break it (optional extras)

- Edit a live object by hand (`kubectl -n linkforge edit deploy/api`, change replicas) and watch
  self-heal revert it. Then turn self-heal off (`kubectl -n argocd patch app linkforge --type merge
  -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":false}}}}'`), change it again, and watch it
  stay drifted (`OutOfSync`). Turn it back on by re-applying `argocd/linkforge-app.yaml`.
- Delete the Application (`kubectl -n argocd delete app linkforge`): the finalizer removes the
  deployed objects, but not the database volume or the secret. Re-apply it to bring everything back.

### Explain it back

1. Push vs pull deployment: why does Argo CD pull from git, and what does that mean for CI's permissions?
2. What do `prune` and `selfHeal` each do? Give one example of each.
3. Why are Postgres and the migration ordered with sync waves, and what did you see change versus plain Helm?
4. In proof 3, why did the site stay up, and why was `git revert` enough to fix it?
5. What is the risk of running `kubectl edit` on a managed app?
6. Where did the image tag in your cluster come from, step by step, starting from your `git push`?

### Recovery: a failed migration left the sync stuck (learned in the P7D drill, 2026-11-07)

Symptom: after a bad commit the Application shows `OutOfSync`/`Synced` with operation `Running` and the
message `waiting for completion of hook batch/Job/migrate`; a `migrate` Job exists with `Error` pods. The old
pods keep serving (that is the design). Reverting the commit alone does NOT fix it, because a running sync
stays pinned to the commit it started with. In order:

1. Revert the bad commit and push (`git revert --no-edit <sha> && git push`).
2. Cancel the operation: `kubectl -n argocd patch app linkforge --type merge -p '{"operation":null}'`
   (answers "no change" if nothing is queued; harmless).
3. If the leftover Job will not go away (`kubectl -n linkforge delete job migrate` says "deleted" but the Job
   stays): Argo puts the finalizer `argocd.argoproj.io/hook-finalizer` on hook Jobs and its own stuck sync is
   waiting for that very Job. Remove it from THAT Job only:
   `kubectl -n linkforge patch job migrate --type merge -p '{"metadata":{"finalizers":null}}'`.
4. If `kubectl -n argocd get app linkforge -o jsonpath='{.status.operationState.phase}'` still says `Running`
   for a Job that no longer exists, clear the stale record:
   `kubectl -n argocd patch app linkforge --type json -p '[{"op":"remove","path":"/status/operationState"}]'`
   and hard-refresh (`kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite`).

Check: all Applications `Synced`/`Healthy`, pods unchanged, the next sync creates a fresh `migrate` Job.

## 4. Runbook: Redis and the P2 settings reach the kind cluster

Goal: the API image built from P2 needs Redis and five new settings. This phase adds them to the
Helm chart so Argo CD deploys them. After it, **login, sessions, rate limits, the redirect cache and
the host guard work on kind**. All commands run in Ubuntu. You push; Argo CD deploys.

### What changed (read the diff first)

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

### 1. Push order matters: gitops first, then the app

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

### 2. Check Redis itself

```bash
kubectl -n linkforge get pods,pvc -l app=redis
kubectl -n linkforge exec redis-0 -- redis-cli ping
kubectl -n linkforge exec redis-0 -- redis-cli config get maxmemory-policy
kubectl -n linkforge exec redis-0 -- redis-cli config get appendonly
```
Expect `PONG`, `noeviction`, `yes`, and a `Bound` 1Gi volume named `data-redis-0`.

### 3. Push the application

```bash
cd ~/linkforge && git push
```
This starts `build-push` (about 5 minutes: tests, Trivy scan, push to GHCR, bump commit in the
gitops repo). Watch the Actions tab. **If the Trivy step fails** on one of the new libraries
(argon2-cffi, redis, prometheus_client), nothing is pushed or deployed; paste me the log.
When the bump commit lands, refresh Argo again (command above) and watch the API roll.

### 4. Prove it works end to end

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
curl -s -b /tmp/kind.jar -X POST $A/api/v1/links -H "$J" -H "$O" -H "X-CSRF-Token: $CSRF" -d '{"destination_url":"https://example.com/from-kind"}' | tee /tmp/kind-link.json
CODE=$(python3 -c 'import json;print(json.load(open("/tmp/kind-link.json"))["code"])'); echo; echo "code=$CODE"
```
Expect two user bodies (register, login), then a link body with a `code`; the last line prints it.
The commands below use `$CODE`, so run them in the same terminal (nothing to paste or replace):

```bash
curl -si http://go.localtest.me/$CODE | head -3
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
for i in $(seq 1 130); do curl -s -o /dev/null -w '%{http_code}\n' http://go.localtest.me/$CODE; done | sort | uniq -c
```
Expect about `120 302` then `10 429`. (Wait a minute before repeating; the window is one minute.)

### 5. Break it (drills; record the results in `docs/RESULTS.md`)

**A. Kill Redis.** Redirects must keep working, logins must fail closed, sessions must survive:

```bash
kubectl -n linkforge delete pod redis-0
```
In another terminal, while it restarts (about 10 s):
```bash
for i in $(seq 1 20); do curl -s -o /dev/null -w '%{http_code} ' http://go.localtest.me/$CODE; sleep 1; done; echo
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

### 6. Record

Resource use with Redis running (idle), for `docs/RESULTS.md`:

```bash
kubectl top pods -n linkforge
kubectl top nodes
```

### Explain it back

1. Why must the gitops chart be pushed before the application?
2. Redis is down: what does a redirect do, what does a login do, and why are they different?
3. What does `noeviction` mean, and what would the app see when Redis is full?
4. Why does the chart carry a hash of the ConfigMap in the API pod template?
5. Why is `TRUSTED_PROXY_COUNT` a `required` value with no default?
6. What makes the go host answer 404 to `/api/v1/links`: the Ingress or the app? Where is the host
   name defined, and how many places must change to rename it?

## 5. Runbook: the click worker and the P3 settings reach the kind cluster

Goal: the API image built from P3 needs one new secret (`IP_HASH_SECRET`) and comes with a second
process, the click worker. This phase adds both to the Helm chart. After it, **every redirect on
kind queues a click, the worker stores it, and the analytics endpoints show it**. All commands run
in Ubuntu. You run the cluster commands and push; Argo CD deploys.

### What changed (read the diff first)

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

### The one dangerous thing: order

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

**Historical note (D71, D72).** The first version of this rollout needed the secret in the migration
Job too, because `alembic` loaded the app's full settings. Since D72 alembic reads only
`DATABASE_URL`, and the chart's Job no longer gets `IP_HASH_SECRET`. That is only safe with an image
that contains D72 (`sha-2313f37` or newer). If you ever see the `migrate` pods in `Error` with
`ip_hash_secret Field required` in `kubectl -n linkforge logs job/migrate`, an image older than D72
is being run by a chart that no longer passes the secret: roll the image forward (or, as a stopgap,
re-add the `ipHashSecretEnv` include to `migrate-job.yaml`). The old API pods keep serving meanwhile.

**If the `migrate` pods keep failing AFTER you pushed the fix:** an Argo sync operation is pinned
to the commit it started with, and its automatic retries reuse that commit, so the old, broken
revision is still being retried. Check with
`kubectl -n argocd get app linkforge -o jsonpath='{.status.sync.revision} {.status.operationState.syncResult.revision}'`
(two different commits = this situation) and cancel the running operation so a fresh sync starts
from the latest commit:

```bash
kubectl -n argocd patch app linkforge --type merge -p '{"operation":null}'
kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite
```

**Local port numbers.** If `docker compose` is running on this machine it already publishes
9101 (API metrics) and 9100 (worker metrics) on localhost, so a port-forward to the same numbers
would clash, or worse, `curl localhost:9101` would show the COMPOSE stack and not the cluster.
This runbook therefore forwards the cluster to **9102** (API) and **9200** (worker).

### 1. Create the secret key (by hand, never in git, never pasted anywhere)

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

### 2. Push the chart (the worker is still off)

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

### 3. Push the application

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
kubectl -n linkforge port-forward svc/api 9102:9101
```
```bash
curl -s localhost:9102/metrics | grep -E '^(http_requests_total|click_stream_)' | head
```
Expect `click_stream_length`, `click_stream_lag` and `click_stream_pending` (all `0.0` for a fresh
stream) and some `http_requests_total{...}` lines with the route `/api/v1/auth/...` or `/readyz`.

### 4. Switch the worker on

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

### 5. Prove it end to end

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
kubectl -n linkforge port-forward svc/worker 9200:9100
```
```bash
curl -s localhost:9200/metrics | grep -E '^(click_events_processed_total|click_stream_)'
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

### 6. Break it (drills; record the results in `docs/RESULTS.md`)

**A. Kill the worker; lag must grow, then drain.**

```bash
kubectl -n linkforge scale deploy/worker --replicas=0
```
Argo's self-heal will put it back to 1 within moments: that is itself the lesson. To watch lag
grow you need the worker gone for longer, so turn it off the GitOps way: set `enabled: false`
again in `envs/kind/values.yaml`, push, refresh, wait for the worker pod to disappear, click the
link 10 times, and read the gauge on the API port (`curl -s localhost:9102/metrics | grep
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

### 7. Record

```bash
kubectl top pods -n linkforge
kubectl top nodes
```
Add the numbers to the "Resource use" section of `docs/RESULTS.md`.

### Explain it back

1. Why do the secret, the chart, the application and the worker have to go in that order, and what
   would each wrong order have done?
2. The worker uses the same image as the API. Which two things in the chart make it a worker?
3. What does `worker.enabled: false` buy you, and why is that safer than deleting the file and
   adding it back later?
4. Why is a Secret not encrypted, and what will replace this by-hand step in P7?
5. Why must the metrics port never appear in an Ingress, and where in the chart is that guaranteed?
6. The stream gauges come from the API as well as the worker. Why, and what must an alert query
   use because of it?
