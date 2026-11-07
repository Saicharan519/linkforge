# Runbook: Argo CD takes over (P1S.5)

Goal: stop deploying by hand. After this phase **a git push is a deploy and `git revert` is a
rollback**. Read `docs/DEVOPS_GUIDE.md` §9 first if you have not. All commands run in Ubuntu.

## The idea in one picture

```
you / CI  --push-->  linkforge-gitops (git)  <--polls every ~3 min--  Argo CD (in the cluster)
                      charts/ + envs/kind/values.yaml                     |  renders the chart
                                                                          v  and makes the cluster match
                                                                     namespace "linkforge"
```

Argo CD runs **inside** the cluster and pulls from git. Nothing from outside needs cluster
credentials, which is exactly why CI never runs `kubectl`. Two behaviours do the real work:
**sync** (apply what git says) and **self-heal** (undo anything someone changes by hand).

## 0. Preparation

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

## 1. Install Argo CD

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

## 2. Open the UI

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

## 3. Hand the app to Argo

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

## 4. The three proofs (the "Done when" of the walking skeleton)

Record each in `docs/RESULTS.md` (a table is there): what you did, what you saw, how long it took.
This is portfolio evidence.

### Proof 1: a code change deploys itself

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

### Proof 2: self-heal (git is the only way to change things)

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

### Proof 3: a bad release does not take you down, and `git revert` heals

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

## 5. Break it (optional extras)

- Edit a live object by hand (`kubectl -n linkforge edit deploy/api`, change replicas) and watch
  self-heal revert it. Then turn self-heal off (`kubectl -n argocd patch app linkforge --type merge
  -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":false}}}}'`), change it again, and watch it
  stay drifted (`OutOfSync`). Turn it back on by re-applying `argocd/linkforge-app.yaml`.
- Delete the Application (`kubectl -n argocd delete app linkforge`): the finalizer removes the
  deployed objects, but not the database volume or the secret. Re-apply it to bring everything back.

## Explain it back

1. Push vs pull deployment: why does Argo CD pull from git, and what does that mean for CI's permissions?
2. What do `prune` and `selfHeal` each do? Give one example of each.
3. Why are Postgres and the migration ordered with sync waves, and what did you see change versus plain Helm?
4. In proof 3, why did the site stay up, and why was `git revert` enough to fix it?
5. What is the risk of running `kubectl edit` on a managed app?
6. Where did the image tag in your cluster come from, step by step, starting from your `git push`?

## Recovery: a failed migration left the sync stuck (learned in the P7D drill, 2026-11-07)

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
