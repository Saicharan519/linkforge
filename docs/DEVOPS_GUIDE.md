# LinkForge — DevOps Guide (for the human, not for Claude)

Purpose: you understand WHAT each DevOps tool does, WHY it's in this project, HOW to run it, HOW it works inside, and how to BREAK it on purpose. Claude Code writes the files; **you run the commands and read the output**. If you can't explain a section in an interview without notes, you haven't learned it yet.

Every section: **What / Why here / How / Verify / How it works / Break it / Interview questions.**

> Verify versions and flags against official docs when you run them — tools move fast. Where I say "check", I mean it.
> **Environment (D20):** run every command in **WSL2 (Ubuntu)**, not PowerShell. Keep the repo outside OneDrive. Cap WSL memory in `%UserProfile%\.wslconfig` (`[wsl2]` / `memory=8GB` / `processors=4`, then `wsl --shutdown`). Where a command says `brew install`, on WSL use the tool's release binary or `apt`/Homebrew-on-Linux instead.
> **D76 changes this guide for this project.** The AWS account is a Builder-ID "new experience" account: its only region is `ap-southeast-2` (Sydney; every `ap-southeast-2` below now says that), and an AWS-managed policy blocks creating the GitHub OIDC provider. So the OIDC + ECR parts of this guide (the CI diagram, §3, §6) describe what a STANDARD account would do and are kept for learning and interviews; they are NOT implemented here. In this project CI pushes to public GHCR and both kind and EKS pull from it.
> **Decisions:** see `docs/DECISIONS.md`. Key ones for this guide: images go to public GHCR for kind and ECR for EKS (D5); Helm values are `envs/kind` and `envs/eks` (D6).

---

## 1. Mental model

```
 you push code
    │
    ▼
 GitHub Actions (CI):  lint → test → trivy fs → build image → trivy image → push to ECR (tag = git SHA)
    │                                                           │
    │ commit new image tag                                      ▼
    ▼                                                         ECR
 linkforge-gitops repo (Helm values)   ◄── Argo CD watches this repo
    │                                          │ renders chart, diffs with cluster, applies
    ▼                                          ▼
   (git = desired state)                Kubernetes (kind locally / EKS on AWS)
                                              │ runs: api, worker, frontend, postgres/redis (kind) or RDS (AWS)
                                              ▼
                                     Prometheus scrapes /metrics → Grafana dashboards → Alertmanager → Slack
 Terraform builds the AWS stuff Kubernetes runs on (VPC, EKS, RDS, ECR, DNS, IAM).
```
- **CI** = prove the change is good and produce an artifact (image). **CD** = make the running system match git. We split them: CI never touches the cluster.
- **Infrastructure as Code (Terraform)** = AWS resources described in files, reviewable, repeatable, destroyable.
- **GitOps** = the gitops repo is the single source of truth for what runs. Rollback is `git revert`.
- **Observability** = you can answer "is it healthy, how fast, what broke" from metrics, not guesses.

Interview one-liner: "Code merges trigger CI that tests, scans, builds an immutable SHA-tagged image and pushes to ECR via OIDC; CI then bumps the tag in a separate gitops repo, and Argo CD reconciles the cluster to it. Terraform provisions the AWS base. Prometheus/Grafana/Alertmanager monitor it."

---

## 2. Docker

**What:** package app + dependencies into an image that runs the same everywhere.
**Why here:** every later step (CI, K8s, ECR) deals in images.

**How (you run):**
```bash
docker compose up -d postgres redis
docker build -t linkforge-api:dev backend/
docker images linkforge-api:dev          # note the size
docker history linkforge-api:dev         # see layers
docker run --rm linkforge-api:dev id     # must NOT be uid=0
docker compose up --build                # whole stack, UI at http://localhost:8080
docker compose logs -f worker
docker compose exec redis redis-cli XLEN clicks
```
**Verify:** app works on :8080; `docker compose ps` shows `healthy` (a container can be *running* but *unhealthy* — healthcheck status is a separate thing; compose `depends_on: condition: service_healthy` uses it); `id` shows non-root; `trivy image linkforge-api:dev` has no unexplained HIGH/CRITICAL; image sizes noted.
**How it works:** an image = stack of read-only layers (each Dockerfile instruction). Multi-stage builds compile/install in a fat stage and copy only results into a slim final stage → smaller, fewer CVEs. A container = image + process + isolated namespaces (pid, net, mount) + cgroup limits. Layer caching: put rarely-changing steps (install deps) before frequently-changing ones (copy source).
**Break it:** change a source file and rebuild — watch which layers are cached. Remove `USER` and see root. Kill postgres while the API runs; read the error.
**Interview Qs:** image vs container? why multi-stage? why non-root? what's in `.dockerignore`? why pin base tags? CMD vs ENTRYPOINT? how does Docker cache layers? how do containers talk in compose (service-name DNS)?

---

## 3. GitHub Actions CI

**What:** automation that runs on git events.
**Why here:** nothing merges unless lint + tests pass; later it builds/scans/pushes images.

**How:** workflows live in `.github/workflows/`. Skeleton (Claude will write the real one):
```yaml
name: ci
on: { pull_request: {}, push: { branches: [main] } }
permissions: { contents: read }
jobs:
  backend:
    runs-on: ubuntu-latest
    services:
      postgres: { image: "postgres:16", env: { POSTGRES_PASSWORD: test, POSTGRES_DB: linkforge_test }, ports: ["5432:5432"], options: >-
        --health-cmd "pg_isready -U postgres" --health-interval 5s --health-retries 10 }
      redis: { image: "redis:7", ports: ["6379:6379"] }
    steps:
      - uses: actions/checkout@<FULL_SHA>   # pin by commit SHA, not @v4
      - uses: actions/setup-python@<FULL_SHA>
      - run: pip install -r backend/requirements.txt -r backend/requirements-dev.txt
      - run: cd backend && ruff check . && ruff format --check . && pytest -q
```
Get a pinned SHA: open the action's GitHub releases page, copy the commit SHA of the release tag, keep the version in a comment. (Dependabot can bump them.)
Also needed (Claude writes them): a **frontend job** (`npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`), plus `pip-audit` and `gitleaks` (secret scan) jobs. Also (D19): `.github/dependabot.yml` (github-actions, pip, npm, docker, terraform ecosystems — otherwise SHA-pinned versions rot), `checkov` on `infra/terraform` (IaC misconfiguration scan), and a gitleaks pre-commit hook. **Image registry (D5):** from the walking skeleton CI pushes to public GHCR using the built-in `GITHUB_TOKEN` (needs `packages: write`); ECR via OIDC is added later in P6.
**Branch protection (do it in GitHub → Settings → Branches, after the first CI run exists):** protect `main`, require pull requests, require the CI status checks to pass, block force-pushes. Without this, CI is advisory only.
**Verify:** push a branch → Actions tab is green; break a test on purpose → red and the PR cannot be merged.
**How it works:** event → runner VM (fresh each time) → jobs/steps. `services:` starts sibling containers (real Postgres/Redis for tests). `permissions:` limits what the auto-issued `GITHUB_TOKEN` can do — default to least privilege.
**Break it:** remove the `permissions` block and see what the default allows; add a failing test; use a floating tag and discuss the supply-chain risk.
**Interview Qs:** CI vs CD? what's a runner? why pin actions by SHA? how do secrets work and why OIDC is better than stored keys? what are service containers?

---

## 4. Trivy (security scanning)

**What:** scanner for vulnerabilities in dependencies and images (and misconfigs/secrets).
**Why here:** "DevSecOps" proof: build fails on serious known CVEs.

**How:**
```bash
trivy fs --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 .
docker build -t linkforge-api:dev backend/
trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 linkforge-api:dev
```
In CI use the official `aquasecurity/trivy-action` (pinned by SHA) or install the CLI; same flags.
**Verify:** green on your image. Then use an old base (`FROM python:3.8`) on a test branch → Trivy fails → that's your demo screenshot.
**How it works:** it unpacks image layers, reads package databases/lockfiles (apt, pip, npm), matches versions against a vulnerability DB. `--ignore-unfixed` hides CVEs with no patch (you can't act on them). `--exit-code 1` makes CI fail. **Limits:** it only knows what its vulnerability database knows (the DB is downloaded fresh on each run — note the DB date in your results), it scans what's *in the image* not how you run it, and a clean scan is not proof of security. False positives/accepted risks go in `.trivyignore` with a comment (why) and an expiry date, reviewed in PRs.
**Break it:** add a known-vulnerable pinned package to requirements; read the finding; fix by upgrading.
**Interview Qs:** what does a scanner actually compare? why ignore-unfixed? what about false positives (use `.trivyignore` with justification + expiry)? where else would you shift security left (secret scanning, SAST, SBOM, image signing)?

---

## 5. Terraform

**What:** declare AWS resources in `.tf` files; Terraform makes reality match.
**Why here:** reproducible, reviewable, destroyable cloud. Destroyability is what protects your wallet.

**Layout:** `bootstrap/` (state bucket) → `foundation/` (OIDC + ECR, cheap, keep) → `cloud/` (VPC/EKS/RDS/DNS, expensive, destroy after use). Separate states so you can destroy `cloud/` safely.

**How (you run; Claude writes the code):**
```bash
# 0. credentials in YOUR shell (SSO/profile), never in the repo
aws sts get-caller-identity               # confirm which account you're about to touch

# 1. bootstrap (local state, once): creates S3 bucket (versioned, encrypted, public access blocked)
cd infra/terraform/bootstrap && terraform init && terraform plan && terraform apply

# 2. foundation: remote state backend
cd ../foundation
# backend block (in backend.tf):
#   terraform { backend "s3" { bucket="linkforge-tfstate-<ACCOUNT_ID>" key="foundation/terraform.tfstate"
#               region="ap-southeast-2" use_lockfile=true encrypt=true } }
terraform init && terraform fmt -check && terraform validate && terraform plan -out tfplan
terraform apply tfplan
terraform plan                           # must say "No changes" (no drift)
```
**Verify:** bucket exists with versioning; ECR repos exist with immutable tags; `terraform plan` clean.
**Bootstrap state:** `bootstrap/` creates the bucket that stores everyone else's state, so it starts with LOCAL state (chicken-and-egg). Keep that small state file safe (or later add a `backend "s3"` block to `bootstrap/` pointing at the same bucket with a different key and run `terraform init -migrate-state`). **Lost/corrupt state:** restore the previous object version from the versioned bucket; if resources exist but state doesn't, `terraform import` them one by one. **Plans can contain secrets** (RDS password): never commit or paste a `tfplan`/`terraform show` output.
**How it works:** `plan` = diff(desired config, state file, real world via provider API). **State** maps your config to real resource IDs; lose it and Terraform forgets what it owns. S3 backend stores state remotely; `use_lockfile` makes Terraform write a lock object in S3 so two runs can't apply at once (DynamoDB locking is deprecated). State contains secrets (e.g. generated RDS password) → private, encrypted bucket. Modules = reusable bundles (we use community modules for VPC/EKS, own modules for the rest). Dependency graph is built from references; `depends_on` only when implicit refs aren't enough.
**Break it:** change a resource in the console → `terraform plan` shows drift → decide: apply (revert) or update code. Run two `apply`s at once → see the lock. `terraform state list`. Delete a resource from code → plan shows destroy.
**Interview Qs:** what is state and why remote? how do you lock state? plan vs apply? what's drift? modules vs workspaces vs separate state dirs? how do you handle secrets in Terraform? what happens if you lose state (`terraform import`)? why separate foundation and cloud?

---

## 6. AWS foundations

**What/why:** safety rails before spending anything; and keyless CI auth.

**Do once (console, ~30 min):**
1. Root account: enable MFA, then stop using root. For daily work use **IAM Identity Center** (short-lived SSO sessions: `aws configure sso`, `aws sso login`). A long-lived IAM user with access keys is the thing we're trying to avoid; if you must use an IAM user for now, MFA + no keys in files, and plan to move to Identity Center.
2. Billing → **Budgets**: create a monthly cost budget with email alerts at $5, $20, $50 (and a forecast alert). Billing → **Free Tier / Credits** page: note what you have and when it expires. A Free Plan account can be closed when credits or the time window end — read the terms on your account page; don't assume "5 months free" covers EKS.
3. Region: `ap-southeast-2` everywhere. Check you're in the right region before you panic about "missing" resources.

**GitHub OIDC (Terraform creates it in `foundation/`):**
- An OIDC identity provider for `token.actions.githubusercontent.com` + an IAM role that GitHub Actions can assume.
- Trust policy (the important bit):
```json
{
  "Effect": "Allow",
  "Principal": { "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com" },
  "Action": "sts:AssumeRoleWithWebIdentity",
  "Condition": {
    "StringEquals": {
      "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub": "repo:OWNER/linkforge:ref:refs/heads/main"
    }
  }
}
```
- Permissions policy: only ECR push to our two repos (least privilege).
- Workflow side: `permissions: { id-token: write, contents: read }`, then `aws-actions/configure-aws-credentials` (with `role-to-assume`) and `aws-actions/amazon-ecr-login` (both pinned by SHA).
**How it works:** GitHub signs a short-lived JWT per workflow run stating repo/branch; AWS verifies it against GitHub's public keys and, if the `sub` matches the trust condition, issues temporary credentials. No long-lived keys exist to leak. Without the `sub` condition, *any* GitHub repo could assume your role — the condition IS the security.
**ECR:** `image_tag_mutability = IMMUTABLE` (a tag can't be overwritten → SHA tags are trustworthy), scan-on-push on, lifecycle policy to keep last ~20 images (storage costs money).
**Verify:** a push to main assumes the role (see the step in the Actions log); a push from a different branch fails to assume it. `aws ecr list-images --repository-name linkforge-api --region ap-southeast-2`.
**Break it:** change the `sub` condition to a wrong branch → assume fails with AccessDenied; read the message.
**Interview Qs:** why OIDC over access keys? what's in the trust policy vs permissions policy? what is `sub`? why immutable tags? what's least privilege here? IAM user vs role vs IRSA (for pods)?

---

## 7. Kubernetes basics + kind

**What:** a system that runs containers across machines and keeps them in the declared state.
**Why here:** it's the standard DevOps interview topic; this project exercises the real pieces.

**Objects you must be able to explain (and where they appear):**
| Object | LinkForge use |
|---|---|
| Pod | smallest unit; 1+ containers |
| Deployment (→ ReplicaSet → Pods) | api, worker, frontend; rolling updates |
| StatefulSet + PVC | postgres, redis on kind (stable identity + disk) |
| Service (ClusterIP) | stable DNS name/IP in front of pods (`api`, `redis`, `postgres`) |
| Ingress + controller | routes `app.*` and `go.*` hosts to services (Traefik on kind, AWS LB Controller on EKS) |
| ConfigMap / Secret | non-secret config / sensitive values |
| Probes | startup, readiness (gets traffic?), liveness (restart me?) |
| Requests/limits | scheduling + OOM/CPU throttling |
| HPA | scale api 2–6 on 70% CPU (needs metrics-server) |
| PDB | keep ≥1 api pod during voluntary disruptions |
| Job / hook | Alembic migration |

**How (you run):**
```bash
kind create cluster --name linkforge --config infra/kind/kind-config.yaml
kubectl cluster-info --context kind-linkforge
kind load docker-image linkforge-api:dev --name linkforge   # local-iteration only: copies a locally built image into the kind nodes
# In the GitOps flow kind pulls the CI-built image from PUBLIC GHCR (no credentials). A private ECR image cannot be pulled by kind without AWS credentials (and ECR tokens expire after 12 h) — that's why ECR is only for EKS (D5).
kubectl get nodes,pods -A
kubectl describe pod <name>        # events at the bottom = first place to look
kubectl logs deploy/api -f
kubectl exec -it deploy/api -- sh
kubectl port-forward svc/api 8000:8000
kubectl top pods                   # needs metrics-server
```
metrics-server on kind needs `--kubelet-insecure-tls` (kind's kubelet certs aren't trusted); that's a local-only workaround — never on EKS. **kind limits:** it's one machine in Docker — no real multi-node scheduling, no cloud load balancer, no real EBS/PV behaviour, shared laptop resources (so `kubectl top` numbers are indicative, not EKS numbers). Check your laptop's Docker memory (≥ 6–8 GiB) before running Argo + monitoring + the app together.
**How it works:** you declare desired state in the API server (stored in etcd). Controllers run reconcile loops: Deployment controller → ReplicaSet → Pods; scheduler assigns pods to nodes; kubelet runs containers. Readiness gates Service endpoints; liveness restarts; startup protects slow boots. Requests = what the scheduler reserves; limits = hard cap (memory over limit → OOMKilled; CPU over → throttled). HPA computes `desired = ceil(current × currentUtil/target)`.
**Break it (these are your drills):** `kubectl delete pod` (watch it come back); set a memory limit of 20Mi (OOMKilled); wrong image tag (ImagePullBackOff); readiness probe to wrong port (pods never Ready, traffic never routed); scale to 0.
**Interview Qs:** Pod vs Deployment? readiness vs liveness? what happens on `kubectl apply` of a Deployment change (rolling update, maxSurge/maxUnavailable)? Service types? how does DNS work in-cluster? requests vs limits? why StatefulSet for DBs (and why you'd use RDS in prod)? what does HPA need?

---

## 8. Helm

**What:** templating + packaging for Kubernetes manifests; a "chart" = templates + `values.yaml`.
**Why here:** one chart deploys kind and EKS; differences live in values files (ingress class, DB host, image repo).

**How:**
```bash
cd linkforge-gitops/charts/linkforge
helm lint .
helm template linkforge . -f ../../envs/kind/values.yaml | less    # see rendered YAML — do this a lot
helm upgrade --install linkforge . -n linkforge --create-namespace -f ../../envs/kind/values.yaml   # first manual try on kind
helm list -n linkforge
helm rollback linkforge 1 -n linkforge
```
(Once Argo CD owns the app, stop using `helm upgrade` by hand — two controllers fighting = confusion.)
**How it works:** Go templates + values → plain manifests → applied. `{{ .Values.x }}`, `{{ include "linkforge.fullname" . }}`, `{{- if .Values.ingress.enabled }}`. Helm stores release history (as Secrets) for rollback; Argo CD instead renders the chart itself and applies via its own sync.
**Break it:** typo in a values key → empty render; remove `required` from a mandatory value; `helm template` with `--set ingress.className=alb` and diff.
**Interview Qs:** Helm vs Kustomize? what is a release? how do you manage per-env differences? what are hooks (we use Argo hooks for the migration)? why not Bitnami charts here (we want to understand and control the Postgres/Redis manifests; also Bitnami's catalog changes — check current status)?

---

## 9. Argo CD (GitOps)

**What:** controller in the cluster that continuously compares git (desired) with the cluster (actual) and syncs.
**Why here:** the "CD" with audit trail, self-healing and git-revert rollback.

**Install and open (you run):**
```bash
kubectl create namespace argocd
kubectl apply -n argocd --server-side -f https://raw.githubusercontent.com/argoproj/argo-cd/<PINNED_VERSION>/manifests/install.yaml
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo
kubectl port-forward svc/argocd-server -n argocd 8443:443     # https://localhost:8443  user: admin
```
(Pin a specific release tag from the Argo CD releases page instead of `stable`.)

**P7C first (simple):** one `linkforge` Application, secrets created by hand, to learn the loop:
```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: { name: linkforge, namespace: argocd }
spec:
  project: default
  source: { repoURL: https://github.com/OWNER/linkforge-gitops, targetRevision: main, path: charts/linkforge,
            helm: { valueFiles: ["../../envs/kind/values.yaml"] } }
  destination: { server: https://kubernetes.default.svc, namespace: linkforge }
  syncPolicy: { automated: { prune: true, selfHeal: true }, syncOptions: ["CreateNamespace=true"] }
```

**P7D final shape — ordering is ENFORCED, not "remember to sync in order":**
A root app-of-apps (`argocd/root.yaml`, the only thing you `kubectl apply` by hand) points at `argocd/apps/`, which holds the child Applications annotated with sync waves:
```
root (app of apps)
 ├─ sealed-secrets   argocd.argoproj.io/sync-wave: "-3"   (controller + SealedSecret CRD)
 ├─ monitoring       argocd.argoproj.io/sync-wave: "-2"   (kube-prometheus-stack → ServiceMonitor/PrometheusRule CRDs)
 └─ linkforge        argocd.argoproj.io/sync-wave: "0"
```
Inside the `linkforge` chart, resources carry their own waves:
```
wave -2  SealedSecrets (+ ConfigMap)
wave -1  Postgres (kind only) + Redis StatefulSets/Services   ← Argo waits until they are Healthy
wave  0  migration Job:  argocd.argoproj.io/hook: Sync
                         argocd.argoproj.io/hook-delete-policy: BeforeHookCreation
wave  1  api, worker, frontend, Services, Ingress, HPA, PDB, ServiceMonitors
```
**Why not `PreSync` for the migration:** PreSync runs before *everything* in the Sync phase. On a fresh cluster Postgres and the Secret wouldn't exist yet, so the Job would fail. A `Sync` hook with a wave runs at the right point. If the migration fails, the sync halts there and the old pods keep serving. Migrations must stay backward compatible because old pods run during the rollout.
**Two things to VERIFY yourself in P7D (don't assume):** (1) does the root app really wait for each child Application to become Healthy between waves? Recent Argo CD versions assess `Application` health out of the box; if yours doesn't, add the documented Lua health check for `Application` to `argocd-cm`. (2) `SealedSecret` has no built-in health check, so the migration pod may start before the unsealed Secret exists — the kubelet retries (`CreateContainerConfigError`) until it appears; confirm it resolves on its own.
**Verify:** on a FRESH kind cluster, `kubectl apply -f argocd/root.yaml` and nothing else → everything comes up in order. Change `image.tag` in git → Argo rolls out.
**How it works:** repo-server renders manifests; application-controller diffs them with live objects; sync applies them wave by wave (waiting for health between waves); `selfHeal` reverts manual drift; `prune` deletes things removed from git. CI never talks to the cluster → no cluster credentials in GitHub.
**Break it:** `kubectl scale deploy/api --replicas=1` → Argo reverts it. Push a bad image tag → pods ImagePullBackOff, old ReplicaSet still serving → `git revert` the tag commit → Argo heals. Make the migration fail → the sync halts at wave 0 and the old pods keep serving. Delete the SealedSecret and watch the order on resync.
**Interview Qs:** push vs pull deployment? what is drift/self-heal? how do you roll back (git revert)? why a separate gitops repo? why must CI and Argo not both deploy? sync waves vs hooks (and why not PreSync here)? why does an Application of Applications need health checks to order things? progressive delivery (Argo Rollouts: mention only)?

---

## 10. Secrets (Sealed Secrets)

**What:** encrypt a Kubernetes Secret so the encrypted form is safe to commit to git; only the in-cluster controller can decrypt.
**Why here:** GitOps means everything lives in git — including secrets, but encrypted.

**How:**
```bash
# install the controller via the Argo app (Helm chart repo https://bitnami-labs.github.io/sealed-secrets) — CHECK the chart's current image registry/tag at install time
# install kubeseal inside WSL: download the Linux binary from the sealed-secrets GitHub releases page and put it on your PATH
kubectl create secret generic linkforge-secrets -n linkforge \
  --from-literal=DATABASE_URL='postgresql+asyncpg://...' \
  --from-literal=IP_HASH_SECRET="$(openssl rand -hex 32)" \
  --dry-run=client -o yaml > /tmp/secret.yaml          # outside the repo!
kubeseal --format yaml --controller-name <name> --controller-namespace <ns> < /tmp/secret.yaml > sealed-linkforge-secrets.yaml
rm /tmp/secret.yaml
git add sealed-linkforge-secrets.yaml                   # safe to commit
```
(`--controller-name/namespace` must match how the chart installed the controller; check `kubectl get pods -A | grep sealed`.)
**How it works:** controller generates a keypair; `kubeseal` encrypts with the public key, bound by default to name+namespace; the controller decrypts into a normal Secret. **Back up the controller's private key** — see the exercise below. Because you will destroy and recreate EKS, this matters: a recreated cluster gets a NEW key and can't decrypt the sealed files already in git unless you restore the old key (or re-seal everything). Anyone with cluster access can still read the decrypted Secret.
**Key backup & restore exercise (do it once in P7D):**
```bash
# find the controller namespace/labels first: kubectl get pods -A | grep sealed
kubectl get secret -n <controller-ns> -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > sealed-secrets-key-backup.yaml
# store OUTSIDE git (password manager / encrypted drive). Anyone with this file can decrypt your sealed secrets.
# restore on a fresh cluster, BEFORE the controller creates its own key (or right after, then restart it):
kubectl apply -f sealed-secrets-key-backup.yaml
kubectl delete pod -n <controller-ns> -l <controller-pod-label>     # controller restarts and loads the restored key
```
Verify: apply one of your committed SealedSecrets on the fresh cluster → a normal Secret appears.
**Production upgrade (mention in interviews):** External Secrets Operator + AWS Secrets Manager: secrets never in git at all, rotation supported.
**Break it:** try to apply a sealed secret in a different namespace → fails to decrypt (scope binding).
**Interview Qs:** why not commit base64 secrets? Sealed Secrets vs External Secrets vs Vault? what is the blast radius of losing the sealing key? how would you rotate a DB password?

---

## 11. Observability (Prometheus, Grafana, Alertmanager)

**What:** metrics collection, dashboards, alerts.
**Why here:** "I measured it" is the difference between a demo and a project.

**How:** install `kube-prometheus-stack` via the `monitoring` Argo app (pin chart version). API and worker expose `/metrics`; `ServiceMonitor` objects tell Prometheus to scrape them (label selector must match the Prometheus `serviceMonitorSelector` — in kube-prometheus-stack that's often `release: <helm release name>`; check values).
```bash
kubectl -n monitoring port-forward svc/<grafana-svc> 3000:80
kubectl -n monitoring port-forward svc/<prometheus-svc> 9090:9090      # Status → Targets must show api & worker UP
```
Dashboard as code: a ConfigMap with label `grafana_dashboard: "1"` is auto-imported by the Grafana sidecar.
Useful PromQL:
```
# redirect p95 (filter by the redirect route so slow analytics endpoints don't distort it)
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{route="/{code}"}[5m])))
# 5xx ratio (only meaningful with traffic; the alert also requires rate > 0.1/s to avoid NaN/noise)
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))
#   alert expr: (that ratio) > 0.02 and sum(rate(http_requests_total[5m])) > 0.1
# cache hit ratio
sum(rate(redirect_cache_total{result="hit"}[5m])) / sum(rate(redirect_cache_total[5m]))
# worker backlog — THREE different things, don't mix them up
max(click_stream_lag)        # not yet delivered to any consumer
max(click_stream_pending)    # delivered but not yet acked (stuck/slow worker shows here)
max(click_stream_length)     # entries in the stream (nearing the 100000 cap = data-loss risk)
# dropped events
sum(rate(click_events_dropped_total[5m]))
```
Alerts (PrometheusRule): see ARCHITECTURE §7.1 (targets vs alert thresholds are deliberately different). Route to Slack via Alertmanager config (webhook URL in a sealed secret).
**How it works:** pull model — Prometheus scrapes HTTP endpoints every N seconds and stores time series. Counters only go up (use `rate()`); histograms store bucket counts so quantiles are estimated at query time — your bucket boundaries decide accuracy. Alerts fire when the PromQL condition holds for the `for:` duration.
**Gotchas:** high-cardinality labels (raw URL paths, user IDs) blow up Prometheus — we label by route template. Request metrics are per-pod; aggregate with `sum`.
**Break it:** stop the worker → lag alert; return 500s deliberately → 5xx alert; check the Slack message arrives.
**Interview Qs:** pull vs push? counter vs gauge vs histogram? how is a quantile computed from buckets? RED vs USE methods? what's a good alert (symptom-based, actionable)? what is cardinality? metrics vs logs vs traces? why no ELK here (cost/complexity; Loki is the lighter option)?

---

## 12. Load testing with k6

**What:** scriptable load generator.
**Why here:** real numbers for your README/resume, and it triggers the HPA.

**Three traps to avoid (these will ruin your numbers):**
1. **Your own rate limiter.** Default limits are 120 redirects/min per IP and 30 creates/min per user. k6 from one machine is ONE IP → you'd measure 429s. For load tests raise them through chart values (`RATE_LIMIT_REDIRECT_PER_MIN` very high, `RATE_LIMIT_CREATE_PER_MIN` high enough), run, then put them back. Test the limiter itself in a separate small test. **Order matters:** `setup()` creates ~1000 links; at the default 30 creates/min that would take 30+ minutes, so raise the create limit BEFORE you start the run.
2. **Mixing scenarios into one number.** Run separate scenarios: (a) warm-cache redirects (one hot code), (b) mixed redirects (≈1000 different codes → realistic hit/miss), (c) link creation at a controlled low rate. Report each separately.
3. **Open-loop vs closed-loop.** Use `constant-arrival-rate` (fixed requests/second) so the request rate is what you say it is, instead of VUs whose rate depends on latency.

**Script sketch (`infra/k6/load.js`):**
```js
import http from 'k6/http'; import { check } from 'k6'; import { Counter } from 'k6/metrics';
const rateLimited = new Counter('rate_limited_429');
export const options = {
  scenarios: {
    redirect_warm:  { executor: 'constant-arrival-rate', rate: 200, timeUnit: '1s', duration: '5m', preAllocatedVUs: 100, maxVUs: 400, exec: 'warm' },
    redirect_mixed: { executor: 'constant-arrival-rate', rate: 50,  timeUnit: '1s', duration: '5m', preAllocatedVUs: 50,  maxVUs: 200, exec: 'mixed' },
    create:         { executor: 'constant-arrival-rate', rate: 20,  timeUnit: '1m', duration: '5m', preAllocatedVUs: 5,   maxVUs: 10,  exec: 'create' }, // 20/min < 30/min limit
  },
  thresholds: {
    'http_req_duration{scenario:redirect_warm}': ['p(95)<250'],   // CLIENT-side; includes your network
    'checks{scenario:redirect_warm}': ['rate>0.99'],
  },
};
export function setup() { /* create ~1000 links with __ENV.API_KEY; return their codes */ }
export function warm()  { const r = http.get(`${__ENV.BASE}/${__ENV.CODE}`, { redirects: 0 });
  if (r.status === 429) rateLimited.add(1); check(r, { 'is 302': x => x.status === 302 }); }
export function mixed(d){ /* GET a random code from d.codes the same way */ }
export function create(){ /* POST /api/v1/links with X-API-Key; expect 201 */ }
```
Run: `BASE=https://go.example.com CODE=abc1234 API_KEY=... k6 run infra/k6/load.js`. `redirects: 0` measures OUR redirect, not the destination site. Only load-test your own system.
**Where you run it matters:** from your laptop the numbers include your internet and Wi-Fi; run k6 from inside the cluster (a k6 Job/pod) as well and compare with the **server-side** histogram in Grafana. State both in the README ("client-side from laptop: …; server-side p95: …").
**Report:** p50/p95/p99, RPS achieved, 5xx rate, `rate_limited_429` (should be 0 in the load scenarios), cache hit ratio, API replicas over time, CPU. Targets: server-side warm-cache p95 < 50 ms is the benchmark target (ARCHITECTURE §7.1) — report the real number even if it misses.
**Break it:** raise the arrival rate until errors appear; find the bottleneck (API CPU? DB connections? Redis?) and say which. Turn the rate limit back on and watch 429s.
**Interview Qs:** what was the bottleneck? p99 vs average? open vs closed workload models? why raise the limiter for the test? why measure from inside the cluster too? what does CPU-based HPA NOT protect you from (Redis latency, DB connections, worker backlog)?

---

## 13. Failure drills (do each; write before/after in `docs/RESULTS.md`)

Pass criteria below are targets **we** chose for this project — adjust if your numbers justify it, but write down what you measured either way. Run traffic (k6, modest rate) during each drill.

| Drill | Command | Pass criteria (measurable) | What it proves |
|---|---|---|---|
| Kill an API pod | `kubectl delete pod -l app=api --wait=false` | 5xx < 1% over the next 2 min; replacement Ready < 60 s; ≥ 1 pod served throughout | replicas, readiness, PDB |
| Kill Redis | `kubectl delete pod redis-0` | redirect success (302) ≥ 99% during the outage; `click_events_dropped_total` rises; logins fail closed; after restart Redis serves again < 60 s; record how many events were lost (enqueued − processed) | fail-open vs fail-closed, AOF recovery, honest data-loss numbers |
| Kill the worker | `kubectl scale deploy/worker --replicas=0`, wait 2 min, scale back | `click_stream_lag` grows ≈ click rate × downtime; after restart lag and pending reach 0 within 5 min; DB click total == clicks k6 sent (± dropped) | at-least-once + durability |
| Redis memory pressure | set a tiny `maxmemory` temporarily | writes fail → `redis_errors_total` rises, redirects still 302, logins fail; restore → recovers | noeviction behaviour |
| Bad image tag | commit tag `does-not-exist` to gitops | 0 failed requests (old pods serve); new pods ImagePullBackOff; `git revert` → Synced/Healthy < 3 min | GitOps rollback, rolling-update safety |
| Failing migration | add a deliberately bad migration | sync halts at the migration step; old ReplicaSet serves 100%; fix-forward commit heals | hook safety, expand/contract |
| Trivy blocks | vulnerable base on a branch | CI red, finding names the CVE/package | shift-left |
| CPU load | k6 high-rate stage | HPA scales 2 → N within ~2–3 min, back down after cool-down (~5 min default) | autoscaling (CPU only) |
| Drift | `kubectl scale` by hand | Argo reverts within its sync interval | self-heal |
| Spoofed IP | send `X-Forwarded-For: 1.2.3.4` with many requests | rate limiter keys on the real client IP, not the spoofed one | trusted-proxy handling |
| Worker crash after commit | automated test (and optionally `kubectl delete pod` the worker mid-load): commit, skip XACK, redeliver | still exactly one `click_events` row per `event_id`; rollup incremented once; `click_events_processed_total{status="duplicate"}` rises | idempotent at-least-once processing (D9) |
| Redis AOF-rewrite memory | `redis-cli BGREWRITEAOF` while writing at load; watch container memory (`kubectl top`, Grafana) | peak working set stays below the container limit; record the peak and set the limit with headroom | memory budget is measured, not guessed (D11) |
For each: what you did, what you saw (screenshot/number), what you'd improve.

---

## 14. Cost control and teardown (read before P9)

**How your "free" account actually works.** New AWS accounts get a pool of **credits** (up to $100 at signup + up to $100 for activities, per AWS's current terms — check *your* Billing → Free Tier/Credits page for your balance and expiry). The free plan ends after 6 months or when credits run out, whichever is first; then the account is suspended and, if you don't upgrade within the grace period (90 days per AWS), erased. Credits are dollars, not free hours: EKS draws them down like anything else, and some services may be blocked on a free-plan account — verify EKS actually works on yours before planning around it.

**What EKS costs (rough; check the AWS pricing pages for ap-southeast-2):** EKS control plane $0.10/hour ≈ $73/month (≈ $2.40/day) — only stops when the cluster is **deleted**; stopping or scaling nodes does NOT stop it. NAT gateway ≈ $33/mo + data, ALB ≈ $16/mo, 2× t3.medium ≈ $60/mo, RDS db.t4g.micro + storage ≈ $15–20/mo. Left running ≈ **$150–200/month**. A 4-hour demo ≈ $1–3 — a few such demos fit inside the credit pool. The killer is forgetting it.
**"Pause" doesn't exist for EKS.** The pattern is *create → use → destroy → recreate later* (that's why `foundation/` is separate from `cloud/`). Expect ~10–15 min to create and ~10–15 min to destroy, and an empty database each time. (RDS can be stopped up to 7 days, but the rest keeps billing — not worth it.)

Rules:
1. Budget alerts set (P0). Know your credit balance before P9.
2. Do P9 + P10 in one sitting. Calendar reminder for the destroy.
3. **Keep the Route53 hosted zone in `foundation/`** ($0.50/mo): its nameservers stay the same across rebuilds, so you set them at your registrar once. `cloud/` only adds records + ACM validation.
4. **Destroy order:** (a) delete the Argo apps (root) so the Ingress is removed and the LB Controller deletes the ALB/target groups — if you `terraform destroy` first, orphaned ALBs/ENIs/security groups can block VPC deletion; (b) `cd infra/terraform/cloud && terraform destroy`; (c) console checklist below; (d) keep `foundation/` (ECR + OIDC + zone ≈ $0.50/mo).
5. **Console checklist after destroy (ap-southeast-2):** EKS clusters · EC2 instances · EBS volumes and snapshots (PVCs create these) · Load Balancers + target groups · NAT gateways · **Elastic IPs** · ENIs/security groups left in the VPC · RDS instances, **snapshots**, retained backups · CloudWatch log groups (EKS/RDS logs) · Route53 *records* in the zone. A domain registration is a **separate purchase** — deleting a hosted zone doesn't cancel it or its renewal.
6. Next day: Billing → Cost Explorer → daily cost ≈ $0 (the zone + a few cents of ECR/S3).
7. **Your credits expire 2027-04-28 (D3)** — balance seen 2026-11-05: $96.23. Schedule the EKS session no later than ~March 2027. A **domain is real money** (check Route53 pricing for your TLD) and credits may not cover it: check before buying. EKS cannot be paused — create, use, destroy.
8. Before creating anything new, open Cost Explorer grouped by **Service** and explain the small charges already on the account (~$2/month seen), so you know what is running.

**Plan B (if EKS isn't affordable, is blocked on your plan, or credits are unclear):** one `t3.small/medium` EC2 running **k3s** (lightweight Kubernetes), same Helm chart + Argo CD, in-cluster Postgres (`postgres.enabled=true`), Traefik ingress (k3s default), DNS A record to the instance's Elastic IP. ≈ $15–30/mo or ≈ $1 for a day demo. You lose "EKS + RDS + ALB" on the resume line; say honestly "k3s on EC2". Terraform still provisions EC2 + SG + EIP.

---

## 15. Troubleshooting cheat sheet

| Symptom | Likely cause | First commands |
|---|---|---|
| `ImagePullBackOff` / `ErrImagePull` | wrong tag/repo, no pull credentials (ECR on EKS: node role needs ECR read; kind: forgot `kind load`) | `kubectl describe pod` → Events |
| `CrashLoopBackOff` | app crashes on start (bad env/secret, DB unreachable, migration not run) | `kubectl logs <pod> --previous` |
| `Pending` | no node has CPU/mem for requests, unbound PVC, taints | `kubectl describe pod`; `kubectl get pvc` |
| `OOMKilled` (Last State) | memory limit too low / leak | `kubectl describe pod`; raise limit, check pool sizes |
| Pod Running but not Ready | readiness probe failing (wrong path/port, DB down) | `kubectl describe pod`; `kubectl logs` |
| Service has no endpoints | selector labels mismatch / pods not ready | `kubectl get endpoints svc/api` |
| Argo `OutOfSync` forever | field mutated by a controller (HPA replicas, defaulted fields) | Argo diff; add `ignoreDifferences` |
| Argo sync stuck at the migration step | migration failed, DB not ready, or the unsealed Secret missing (check sync waves, §9) | `kubectl logs job/<migrate>`; `kubectl get sealedsecret,secret -n linkforge` |
| HPA shows `<unknown>` | metrics-server missing or no CPU requests set | `kubectl top pods`; check requests |
| ALB not created (EKS) | AWS LB Controller missing/IRSA wrong/subnet tags missing/wrong ingressClass+annotations | `kubectl logs -n kube-system deploy/aws-load-balancer-controller` |
| Prometheus target DOWN / absent | ServiceMonitor label doesn't match Prometheus selector, wrong port name | Prometheus UI → Status → Targets/Config |
| `terraform destroy` hangs | orphaned ALB/ENI/SG in VPC | console: delete leftovers; see §14 order |
| Terraform "state lock" error | previous run crashed | check S3 lock object; `terraform force-unlock <ID>` only if sure |
| Too many DB connections | pool × replicas > `max_connections` | lower `DB_POOL_SIZE`, check HPA max |

---

## 16. DevOps interview Q&A (say these out loud)

1. **Walk me through your pipeline.** (§1 one-liner, then detail each gate.)
2. **How do you roll back?** `git revert` the tag bump in the gitops repo; Argo reconciles. Old ReplicaSet kept serving if new pods never became ready.
3. **Why separate app and gitops repos?** CI commits don't retrigger CI; audit trail; CD credentials stay out of CI.
4. **How do you handle secrets?** No secrets in code/images; Sealed Secrets for GitOps; OIDC for CI→AWS; production would use External Secrets + Secrets Manager.
5. **What happens if Redis dies?** Matrix in ARCHITECTURE §8 (and the honest stream-cap caveat in §2.3.1).
6. **How do you do zero-downtime deploys?** Rolling update + readiness probes + PDB + backward-compatible migrations (expand/contract) run by a sync-wave hook Job before the new Deployments roll out.
7. **How does autoscaling work and what did you observe?** HPA on CPU 70% needs requests + metrics-server; cite your k6 numbers.
8. **How do you keep the cloud bill under control?** Budgets, time-boxed deploy, Terraform destroy, separate foundation state, Plan B.
9. **What would you change for real production?** Multi-AZ RDS, ElastiCache, External Secrets, WAF, image signing (cosign), PodSecurity admission, network policies, Argo Rollouts, Loki/tracing, backup/restore drills, multi-env (staging).
10. **Terraform state?** §5.
11. **What did Trivy catch / what would you do about an unfixable CVE?** Evidence from your drill; `.trivyignore` with justification and expiry date; or swap base image.
12. **Why not Jenkins/Kafka/Istio?** Complexity not justified by the problem; GitHub Actions covers CI; Redis Streams covers the queue; no service mesh needed for 2 services. Knowing when NOT to use a tool is the senior answer.

Final test: explain the diagram in §1 to a friend with the laptop closed. Where you get stuck, reread that section and redo its "Break it".
