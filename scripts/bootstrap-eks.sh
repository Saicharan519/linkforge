#!/usr/bin/env bash
# bootstrap-eks.sh: hand a fresh EKS cluster to Argo CD (P9.3). The EKS counterpart of bootstrap-kind.sh. YOU run it,
# after `terraform apply` in infra/terraform/cloud has finished and you have the EKS kubeconfig:
#
#   export KUBECONFIG=~/.kube/linkforge-eks.yaml
#   scripts/bootstrap-eks.sh 2>&1 | tee ~/runlogs/bootstrap-eks.log
#
# What it does (nothing is created in AWS; Terraform did that):
#   1 checks that kubectl really points at the EKS cluster and the nodes are Ready
#   2 installs Argo CD v3.6.1, pinned to the exact git commit and checked against a sha256 (same as kind)
#   3 teaches Argo CD to judge the health of `Application` objects, so waves between child apps are enforced
#   4 applies the EKS root Application (argocd/eks/root.yaml), the one manual apply; everything else follows from git
#   5 waits for the four platform apps (wave -3): sealed-secrets, eks-storage, metrics-server, aws-load-balancer-controller
#   6 makes the load balancer controller's webhooks trust the certificate it serves (eks-fix-webhook-ca.sh, D90): without
#     it the first Ingress can be rejected with "x509: certificate signed by unknown authority"
#
# It does NOT deploy LinkForge: the `linkforge` Application waits in argocd/eks/pending/ until the secrets are sealed
# against THIS cluster's key (scripts/eks-seal-and-enable.sh). Unlike kind there is no key to restore: the EKS cluster
# makes its own sealing key, and nothing sealed for kind can be read here.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/eks-common.sh"

GITOPS="${GITOPS_DIR:-$HOME/linkforge-gitops}"
ARGO_COMMIT=94037c4d4e1af4bd0651d37a179fbd9e347e668c # v3.6.1
ARGO_SHA256=7be23ca7d9ca3d3074dee8332f1ca92a933b990cee7c9cf22cf3843ee8c2913e
PLATFORM_APPS="sealed-secrets eks-storage metrics-server aws-load-balancer-controller"
WAIT_SECONDS="${WAIT_SECONDS:-900}"

for tool in kubectl curl sha256sum; do command -v "$tool" > /dev/null || die "missing tool: $tool"; done
[ -f "$GITOPS/argocd/eks/root.yaml" ] || die "gitops repo not found at $GITOPS (set GITOPS_DIR)"

step "1/6 is kubectl pointed at the EKS cluster?"
require_eks_cluster
ek get nodes -o wide
ek wait --for=condition=Ready node --all --timeout=300s

step "2/6 Argo CD v3.6.1 (commit ${ARGO_COMMIT:0:7}, sha256 checked)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL --retry 5 --retry-all-errors -o "$TMP/argocd-install.yaml" \
  "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGO_COMMIT}/manifests/install.yaml"
echo "${ARGO_SHA256}  $TMP/argocd-install.yaml" | sha256sum --check --strict -
ek get namespace argocd > /dev/null 2>&1 || ek create namespace argocd
ek apply -n argocd --server-side --force-conflicts -f "$TMP/argocd-install.yaml" > /dev/null
ek -n argocd rollout status deploy/argocd-server deploy/argocd-repo-server deploy/argocd-redis --timeout=300s
ek -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
# three components this project does not use (single sign-on, chat notifications, ApplicationSets): fewer pods
ek -n argocd scale deploy argocd-dex-server argocd-notifications-controller argocd-applicationset-controller --replicas=0 > /dev/null

step "3/6 Argo health check for Application resources"
ek apply --server-side --force-conflicts -f "$GITOPS/argocd/bootstrap/argocd-cm-health.yaml" > /dev/null
echo "applied (the root now waits for each child Application to be Healthy)"

step "4/6 root Application (the one manual apply)"
ek apply -f "$GITOPS/argocd/eks/root.yaml"

step "5/6 waiting for the platform apps (up to $((WAIT_SECONDS / 60)) minutes)"
deadline=$((SECONDS + WAIT_SECONDS))
while :; do
  all_ok=1
  line=""
  for app in $PLATFORM_APPS; do
    st="$(ek -n argocd get application "$app" -o jsonpath='{.status.sync.status}/{.status.health.status}' 2> /dev/null || true)"
    st="${st:-absent}"
    line="$line $app=$st"
    [ "$st" = "Synced/Healthy" ] || all_ok=0
  done
  printf '[%s]%s\n' "$(date +%H:%M:%S)" "$line"
  [ "$all_ok" = 1 ] && break
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "NOT all platform apps are Healthy after $((WAIT_SECONDS / 60)) minutes. Look at:" >&2
    echo "  kubectl --kubeconfig \"$EKS_KUBECONFIG\" -n argocd get applications" >&2
    echo "  kubectl --kubeconfig \"$EKS_KUBECONFIG\" -n kube-system logs deploy/aws-load-balancer-controller   (region/VPC autodiscovery? Pod Identity?)" >&2
    exit 1
  fi
  sleep 15
done

step "6/6 webhook certificate authority of the load balancer controller"
"$(dirname "${BASH_SOURCE[0]}")/eks-fix-webhook-ca.sh"

echo
echo "Done. Platform is up: Sealed Secrets, gp3 StorageClass, metrics-server, AWS Load Balancer Controller."
echo "Next: put your IP in envs/eks/values.yaml (inbound-cidrs, replace REPLACE_WITH_YOUR_IP/32), push, then run scripts/eks-seal-and-enable.sh"
