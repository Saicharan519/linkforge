# Runbook: the same deployment, as a Helm chart (P1S.4c)

The chart lives in the **other** repo, `~/linkforge-gitops` (`charts/linkforge/`), and the kind
settings in `envs/kind/values.yaml`. Read the templates first; each carries comments on why.
Reference: `docs/DEVOPS_GUIDE.md` §8. Run everything in Ubuntu.

## What Helm is, in one paragraph

A chart is a folder of **templates** (YAML with `{{ }}` holes) plus default **values**. Helm
fills the holes and applies the result. `helm template` only renders (changes nothing), which is
how you learn what a chart does. `helm install` renders and applies, and remembers the result as a
**release** with a numbered history, so `helm rollback` works. Environment differences live only
in the values file you pass with `-f`; the templates never change between kind and EKS.

## 1. Look before you apply

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

## 2. Prepare the namespace and secret

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

## 3. Install

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

## 4. Break it

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

## 5. Hand over to Argo CD (do this right before P1S.5, not now)

Two controllers managing the same objects fight each other, so once Argo owns the app, Helm must
let go. Removing the release deletes the objects it created, but **not** the namespace, the secret
or the database volume (Helm never owned those), so the database password stays valid:

```bash
helm uninstall linkforge -n linkforge
```

## Explain it back

1. What is the difference between a chart, a release, and a values file?
2. `helm template` vs `helm install`: what does each change?
3. Why does the chart not contain the Secret, and what will contain it later?
4. What did drill B show about Helm and typos, and what protects you?
5. Why did drill E fail under plain Helm, and what makes it work under Argo CD?
6. Where does the image tag come from, and who changes it?
