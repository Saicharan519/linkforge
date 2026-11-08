# Runbook: the local Kubernetes (kind) cluster

This file merges the former `RUNBOOK-bootstrap-kind.md` and `RUNBOOK-kind.md` (2026-11-09; nothing was dropped). Part 1 is the normal way (one command);
Part 2 is the manual detail behind it.

## Part 1. Build the whole local cluster from nothing (one command)

When: a new machine, a broken cluster, or to prove that git + the sealing-key backup really are enough.
Everything below was exercised twice on 2026-11-07 (docs/DECISIONS.md D78).

### What you need
- Docker, `kind`, `kubectl`, `helm`, `python3`, `curl` (all in `~/.local/bin` or on PATH).
- The repositories `~/linkforge` and `~/linkforge-gitops` (the latter is where Argo CD reads from, on GitHub).
- **The sealing-key backup** (`~/.sealed-secrets-backup/sealing-key-*.yaml`, or your safe copy). Without it the
  SealedSecrets in git cannot be decrypted: a new cluster invents a new key. Never put this file in git.

### Run it
```bash
cd ~/linkforge
scripts/bootstrap-kind.sh --key-backup ~/.sealed-secrets-backup/sealing-key-YYYYMMDD-HHMM.yaml
```
To start from a clean slate first: `kind delete cluster --name linkforge` (this erases the database inside it).
It takes about 5 minutes for the script and about 3 more until Argo CD reports everything Healthy.

### What it does, in order (and why that order)
1. kind cluster (node image pinned by digest). 2. Traefik. 3. metrics-server (kind-only `--kubelet-insecure-tls`).
4. Argo CD v3.6.1 (exact commit, sha256 checked). 5. The Argo health check for Applications, so the root waits
for each child to be Healthy. 6. The sealing key is restored BEFORE the controller exists, so it finds and
uses the old key. 7. `root.yaml`, the only manual apply: it creates `sealed-secrets` (wave -3), then
`linkforge` (wave 0); inside linkforge: secrets and ConfigMap (-2), Postgres and Redis (-1), the migration
hook (0), the workloads (1).

### Check
```bash
kubectl --context kind-linkforge -n argocd get applications.argoproj.io      # root, sealed-secrets, linkforge: Synced / Healthy
kubectl --context kind-linkforge -n linkforge get pods                       # 7 pods; migrate is Completed
curl -s -o /dev/null -w '%{http_code}\n' http://app.localtest.me/             # 200
curl -s -o /dev/null -w '%{http_code}\n' http://app.localtest.me/api/v1/auth/me   # 401 (not signed in)
```
A minute of `Degraded` right at the end is normal (the HPA has no metrics yet); it clears by itself.

### If something fails
- `--skip-health-fix` exists only to reproduce the experiment that shows why the health check is needed.
- A stuck or failed migration: docs/LEARNING-WALKTHROUGHS.md, section "Recovery: a failed migration left the sync stuck".
- The script is safe to re-run: every step checks first. It only talks to the context `kind-linkforge`.

## Part 2. Install and create it by hand (what the script automates)

Everything here runs in your **Ubuntu (WSL) terminal**. Versions are pinned on purpose; bump them
deliberately, not by accident. Background reading: `docs/DEVOPS_GUIDE.md` §7 and §8.

| Piece | Version | Why this one |
|---|---|---|
| kind | v0.33.0 | creates a cluster inside Docker |
| Kubernetes (node image) | v1.35.8, pinned by digest in `infra/kind/kind-config.yaml` | within one minor of the 1.36 `kubectl` in WSL |
| helm | v3.22.1 | the version family Argo CD uses |
| Traefik chart | traefik/traefik 42.2.0 (Traefik v3.7.16) | ingress controller |
| metrics-server | v0.9.1 | makes `kubectl top` and, later, the HPA work |

### 1. Install kind and helm

`helm` was already installed to `~/.local/bin` while preparing this phase. Check both:

```bash
helm version --short
```

If it says "command not found", open a new terminal (Ubuntu adds `~/.local/bin` to PATH at login
when the folder exists) or run `export PATH="$HOME/.local/bin:$PATH"`.

Install kind. The checksum line is the safety check: if the download was tampered with or
truncated, `sha256sum` fails and nothing gets installed.

```bash
cd /tmp && curl -fLo kind https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64
```
```bash
echo "aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d  kind" | sha256sum --check
```
```bash
chmod +x kind && mkdir -p ~/.local/bin && mv kind ~/.local/bin/kind && kind version
```

### 2. Create the cluster

```bash
cd ~/linkforge && kind create cluster --config infra/kind/kind-config.yaml
```

The first run downloads the node image (~1 GB) and takes a few minutes. Then:

```bash
kubectl config current-context        # expect: kind-linkforge
kubectl get nodes                      # expect: linkforge-control-plane   Ready
kubectl get pods -A                    # system pods (coredns, etcd, ...) all Running
```

### 3. Install Traefik (the ingress controller)

```bash
helm repo add traefik https://traefik.github.io/charts && helm repo update
```
```bash
helm install traefik traefik/traefik --version 42.2.0 \
  --namespace traefik --create-namespace \
  -f infra/kind/traefik-values.yaml
```
```bash
kubectl -n traefik rollout status deploy/traefik
kubectl -n traefik get svc traefik     # expect TYPE NodePort, PORT(S) 80:30080/TCP
kubectl get ingressclass               # expect: traefik (default)
```

Prove the whole path (browser -> host port 80 -> node -> Traefik) works. No Ingress rules exist
yet, so Traefik correctly answers "404 page not found". That 404 comes FROM Traefik, which is
the proof that traffic reaches it:

```bash
curl -i http://localhost/
```

### 4. Install metrics-server

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.1/components.yaml
```

kind's kubelet uses a self-signed certificate that metrics-server does not trust. For LOCAL kind
only, tell it to skip that check. Never do this on EKS, whose kubelets have proper certificates.

```bash
kubectl -n kube-system patch deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```
```bash
kubectl -n kube-system rollout status deploy/metrics-server
sleep 30 && kubectl top nodes          # expect CPU and memory numbers, not an error
```

("error: Metrics API not available" in the first minute is normal; retry after a moment.)

### 5. Tear down / reset

```bash
kind delete cluster --name linkforge   # removes the whole cluster; everything in it is gone
```

Recreating takes a couple of minutes, so it is a cheap, safe way to start clean.
