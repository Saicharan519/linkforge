# Runbook: build the whole local cluster from nothing (one command)

When: a new machine, a broken cluster, or to prove that git + the sealing-key backup really are enough.
Everything below was exercised twice on 2026-11-07 (docs/DECISIONS.md D78).

## What you need
- Docker, `kind`, `kubectl`, `helm`, `python3`, `curl` (all in `~/.local/bin` or on PATH).
- The repositories `~/linkforge` and `~/linkforge-gitops` (the latter is where Argo CD reads from, on GitHub).
- **The sealing-key backup** (`~/.sealed-secrets-backup/sealing-key-*.yaml`, or your safe copy). Without it the
  SealedSecrets in git cannot be decrypted: a new cluster invents a new key. Never put this file in git.

## Run it
```bash
cd ~/linkforge
scripts/bootstrap-kind.sh --key-backup ~/.sealed-secrets-backup/sealing-key-YYYYMMDD-HHMM.yaml
```
To start from a clean slate first: `kind delete cluster --name linkforge` (this erases the database inside it).
It takes about 5 minutes for the script and about 3 more until Argo CD reports everything Healthy.

## What it does, in order (and why that order)
1. kind cluster (node image pinned by digest). 2. Traefik. 3. metrics-server (kind-only `--kubelet-insecure-tls`).
4. Argo CD v3.6.1 (exact commit, sha256 checked). 5. The Argo health check for Applications, so the root waits
for each child to be Healthy. 6. The sealing key is restored BEFORE the controller exists, so it finds and
uses the old key. 7. `root.yaml`, the only manual apply: it creates `sealed-secrets` (wave -3), then
`linkforge` (wave 0); inside linkforge: secrets and ConfigMap (-2), Postgres and Redis (-1), the migration
hook (0), the workloads (1).

## Check
```bash
kubectl --context kind-linkforge -n argocd get applications.argoproj.io      # root, sealed-secrets, linkforge: Synced / Healthy
kubectl --context kind-linkforge -n linkforge get pods                       # 7 pods; migrate is Completed
curl -s -o /dev/null -w '%{http_code}\n' http://app.localtest.me/             # 200
curl -s -o /dev/null -w '%{http_code}\n' http://app.localtest.me/api/v1/auth/me   # 401 (not signed in)
```
A minute of `Degraded` right at the end is normal (the HPA has no metrics yet); it clears by itself.

## If something fails
- `--skip-health-fix` exists only to reproduce the experiment that shows why the health check is needed.
- A stuck or failed migration: docs/RUNBOOK-argocd.md, section "Recovery: a failed migration left the sync stuck".
- The script is safe to re-run: every step checks first. It only talks to the context `kind-linkforge`.
