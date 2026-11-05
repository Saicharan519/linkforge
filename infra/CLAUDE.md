# Infra rules (Terraform, Docker, K8s, CI)

## Layout (this repo)
```
infra/
  terraform/
    bootstrap/     # S3 state bucket (local state, run once)
    foundation/    # GitHub OIDC provider+role, ECR repos, Route53 hosted zone (added in P9 prereq)  — cheap, keep forever
    cloud/         # VPC, EKS, RDS, ACM + DNS records, LB-controller IRSA      — costs money, destroy after demo
    modules/       # ecr/ github-oidc/ rds/ dns/ (own modules). VPC+EKS use terraform-aws-modules.
  kind/            # kind-config.yaml, metrics-server patch notes
  k6/              # load test scripts
scripts/           # pg_dump.sh, restore, etc.
.github/workflows/ # ci.yml (lint+test+frontend), build-push.yml (trivy+image+ecr+gitops bump)
docker-compose.yml
```
Separate repo `linkforge-gitops`: `charts/linkforge/`, `envs/kind/values.yaml`, `envs/eks/values.yaml` (never one shared file, D6), `argocd/` (root app-of-apps + child Applications).

## Hard rules
- NEVER run `terraform apply|destroy`, mutating `aws` commands, or `kubectl` against a cloud cluster. NEVER ask for, request, read, or store credentials, and never read `~/.aws`. Write code; run only offline checks (`terraform fmt`, `terraform validate` after `init -backend=false`, `helm lint`, `helm template`). Print the exact command for the human to run.
- No AWS access keys anywhere. GitHub Actions uses OIDC (`permissions: id-token: write, contents: read`), trust restricted to `repo:OWNER/REPO:ref:refs/heads/main`.
- Terraform >= 1.11. S3 backend `use_lockfile = true`, `encrypt = true`. No DynamoDB. State holds secrets (RDS password): bucket private, versioned, encrypted, public access blocked. Never commit or paste `tfplan` files.
- Separate state keys: `foundation/terraform.tfstate`, `cloud/terraform.tfstate`. `cloud/` must be destroyable without touching `foundation/`. The Route53 hosted zone lives in `foundation/` so nameservers don't change on every rebuild; `cloud/` only adds records and the ACM cert validation.
- Tag every AWS resource: `Project=linkforge`, `Env=prod`, `ManagedBy=terraform`.
- EKS API endpoint: public access restricted via `cluster_endpoint_public_access_cidrs` (variable, the human's IP /32).
- RDS (demo): private subnets, `backup_retention_period = 1`, `skip_final_snapshot = true`, `deletion_protection = false` — with a comment saying why (disposable) and what production would do.
- Pin everything: provider versions (`~>`), module versions, Docker base tags (no `latest`), GitHub Actions by full commit SHA, Helm chart versions, Argo CD version.
- Docker: multi-stage, non-root, `.dockerignore`, `HEALTHCHECK` for compose, no secrets in layers. One backend image, API and worker via different `command`.
- Image tag = short git SHA. Never deploy `latest`. Registries (D5): CI pushes to **public GHCR** with `GITHUB_TOKEN` from P1.5 (kind pulls it; `kind load` only for local iteration); P6 adds an OIDC push to **ECR** (immutable tags) which EKS pulls natively.
- Also in CI/repo (D19): Dependabot (actions, pip, npm, docker, terraform), Checkov on `infra/terraform`, gitleaks pre-commit. k3s on EC2 is a documented fallback only (D2): do not build it unless EKS is blocked.
- CI gates: ruff + pytest (service containers) + frontend lint/typecheck/test/build; then Trivy `fs` and `image` with `--severity HIGH,CRITICAL --ignore-unfixed --exit-code 1`. Exceptions only via `.trivyignore` with justification + expiry date.
- CD = Argo CD only. CI never runs `kubectl`/`helm`; it builds, scans, pushes, and commits the new tag to `linkforge-gitops` (fine-grained token scoped to that one repo).
- Helm chart: ingress className configurable (`traefik` on kind, `alb` on EKS); the `go` host Ingress points to the API http port only; metrics ports are named `metrics` and appear in NO Ingress; resource requests/limits on every container (budget in ARCHITECTURE §9.3); securityContext (runAsNonRoot, drop ALL caps, readOnlyRootFilesystem where possible); startup+readiness+liveness probes; HPA (70% CPU, min 2, max 6) + PDB (minAvailable 1) for the API; `postgres.enabled` toggle (true on kind, false on EKS).
- Deployment ordering is ENFORCED by sync waves exactly as ARCHITECTURE §11 (root app-of-apps waves: sealed-secrets -3, monitoring -2, linkforge 0; inside linkforge: secrets -2, Postgres/Redis -1, migration Sync-hook Job 0, workloads 1). Do not use PreSync for the migration.
- No Bitnami charts for Postgres/Redis. Official images in StatefulSets inside our chart.
- Secrets: Sealed Secrets. Document the controller key backup procedure (DEVOPS_GUIDE §10).
- Comment every non-obvious Terraform/Helm block with WHY. The human is learning from this code.
