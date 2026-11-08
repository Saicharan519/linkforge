#!/usr/bin/env bash
# bootstrap-kind.sh: build the local kind cluster from nothing and hand it to Argo CD.
#
# Every step is one the runbooks already describe (docs/RUNBOOK-kind-cluster.md, LEARNING-WALKTHROUGHS.md); this script
# only puts them in the one order that works, with the versions pinned, so a new cluster is ONE command:
#
#   scripts/bootstrap-kind.sh --key-backup ~/.sealed-secrets-backup/sealing-key-YYYYMMDD-HHMM.yaml
#
# Order and why:
#   1 kind cluster            the node image is pinned by digest in infra/kind/kind-config.yaml
#   2 Traefik                 the ingress controller (NodePort 30080 <- host port 80)
#   3 metrics-server          kubectl top and the HPA
#   4 Argo CD                 pinned to the exact git commit of v3.6.1 and checked against a sha256
#   5 Argo health check       so the root app-of-apps waits for a CHILD Application to be Healthy
#   6 sealing key restore     BEFORE the Sealed Secrets controller exists: it then finds the old key and
#                             uses it, so the SealedSecrets already in git can be decrypted. Without it the
#                             controller invents a NEW key and every SealedSecret stays undecryptable.
#   7 root.yaml               the one manual apply; everything else follows from git, in wave order.
#
# Safe by construction: it only ever talks to the context `kind-linkforge` (explicit --context on every
# kubectl/helm call) and refuses to run if that context is not a local kind cluster.
set -euo pipefail

CLUSTER=linkforge
CTX="kind-${CLUSTER}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GITOPS="${GITOPS_DIR:-$HOME/linkforge-gitops}"
ARGO_COMMIT=94037c4d4e1af4bd0651d37a179fbd9e347e668c # v3.6.1
ARGO_SHA256=7be23ca7d9ca3d3074dee8332f1ca92a933b990cee7c9cf22cf3843ee8c2913e

KEY_BACKUP=""
HEALTH_FIX=1
while [ $# -gt 0 ]; do
  case "$1" in
    --key-backup) KEY_BACKUP="$2"; shift 2 ;;
    --skip-health-fix) HEALTH_FIX=0; shift ;; # only for the experiment that proves why the fix exists
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n[%s] === %s\n' "$(date +%H:%M:%S)" "$*"; }
k() { kubectl --context "$CTX" "$@"; }

for tool in kind kubectl helm docker python3 curl sha256sum; do
  command -v "$tool" > /dev/null || { echo "missing tool: $tool" >&2; exit 1; }
done
[ -d "$GITOPS" ] || { echo "gitops repo not found at $GITOPS (set GITOPS_DIR)" >&2; exit 1; }

step "1/7 kind cluster"
if kind get clusters 2> /dev/null | grep -qx "$CLUSTER"; then
  echo "cluster '$CLUSTER' already exists, reusing it"
else
  kind create cluster --config "$REPO_ROOT/infra/kind/kind-config.yaml"
fi
# the guard: whatever happened above, we only continue against the local kind cluster
[ "$(kubectl config get-contexts -o name | grep -x "$CTX" || true)" = "$CTX" ] || { echo "context $CTX missing" >&2; exit 1; }
k get nodes

step "2/7 Traefik 42.2.0"
helm repo add traefik https://traefik.github.io/charts > /dev/null 2>&1 || true
helm repo update > /dev/null
helm --kube-context "$CTX" upgrade --install traefik traefik/traefik --version 42.2.0 \
  --namespace traefik --create-namespace -f "$REPO_ROOT/infra/kind/traefik-values.yaml" > /dev/null
k -n traefik rollout status deploy/traefik --timeout=180s

step "3/7 metrics-server v0.9.1 (kind only: --kubelet-insecure-tls, its kubelet cert is self-signed)"
k apply -f https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.1/components.yaml > /dev/null
if ! k -n kube-system get deploy metrics-server -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -q kubelet-insecure-tls; then
  k -n kube-system patch deployment metrics-server --type=json \
    -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' > /dev/null
fi
k -n kube-system rollout status deploy/metrics-server --timeout=180s

step "4/7 Argo CD v3.6.1 (commit ${ARGO_COMMIT:0:7}, sha256 checked)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL --retry 5 --retry-all-errors -o "$TMP/argocd-install.yaml" \
  "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGO_COMMIT}/manifests/install.yaml"
echo "${ARGO_SHA256}  $TMP/argocd-install.yaml" | sha256sum --check --strict -
k get namespace argocd > /dev/null 2>&1 || k create namespace argocd
k apply -n argocd --server-side --force-conflicts -f "$TMP/argocd-install.yaml" > /dev/null
k -n argocd rollout status deploy/argocd-server deploy/argocd-repo-server deploy/argocd-redis --timeout=300s
k -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
# three components this project does not use (single sign-on, chat notifications, ApplicationSets)
k -n argocd scale deploy argocd-dex-server argocd-notifications-controller argocd-applicationset-controller --replicas=0 > /dev/null

step "5/7 Argo health check for Application resources"
if [ "$HEALTH_FIX" = 1 ]; then
  k apply --server-side --force-conflicts -f "$GITOPS/argocd/bootstrap/argocd-cm-health.yaml" > /dev/null
  echo "applied (the root now waits for each child Application to be Healthy)"
else
  echo "SKIPPED on request (--skip-health-fix): the root will NOT wait for children"
fi

step "6/7 Sealed Secrets key restore"
if [ -n "$KEY_BACKUP" ]; then
  [ -f "$KEY_BACKUP" ] || { echo "key backup not found: $KEY_BACKUP" >&2; exit 1; }
  # The backup is a `kubectl get -o yaml` list. Drop the fields that belong to the OLD cluster (uid,
  # resourceVersion, ...) so it can be created in this one. Nothing is printed: it is a private key.
  python3 - "$KEY_BACKUP" > "$TMP/key.yaml" << 'EOF'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
items = d["items"] if d.get("kind") == "List" else [d]
for it in items:
    for f in ("uid", "resourceVersion", "creationTimestamp", "ownerReferences", "managedFields"):
        it["metadata"].pop(f, None)
    it["metadata"]["namespace"] = "kube-system"
print(yaml.safe_dump({"apiVersion": "v1", "kind": "List", "items": items}))
EOF
  k apply -f "$TMP/key.yaml" > /dev/null
  rm -f "$TMP/key.yaml"
  echo "restored $(k -n kube-system get secret -l sealedsecrets.bitnami.com/sealed-secrets-key --no-headers | wc -l) sealing key secret(s) into kube-system"
else
  echo "no --key-backup given: the controller will create a NEW key; existing SealedSecrets will NOT decrypt"
fi

step "7/7 root Application (the one manual apply)"
k apply -f "$GITOPS/argocd/root.yaml"
echo
echo "Done. Argo CD now builds the rest from git, in wave order. Watch it:"
echo "  kubectl --context $CTX -n argocd get applications.argoproj.io -w"
