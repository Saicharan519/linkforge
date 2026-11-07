#!/usr/bin/env bash
# eks-verify.sh: read-only checks of the P9 "done when" list on the running EKS deployment (HTTP mode). YOU run it:
#
#   export KUBECONFIG=~/.kube/linkforge-eks.yaml
#   scripts/eks-verify.sh 2>&1 | tee ~/runlogs/eks-verify.log
#   (or give the names:  scripts/eks-verify.sh APP_DNS GO_DNS )
#
# It changes nothing. It prints PASS/FAIL per check and exits non-zero if any failed. NOT covered: that clicks show up in
# analytics (create a link in the UI, open the short link, look at the analytics page: that needs a human login).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/eks-common.sh"

command -v curl > /dev/null || die "curl is missing"
FAILED=0
check() { # check "name" "expected" "actual"
  if [ "$2" = "$3" ]; then printf 'PASS  %-58s %s\n' "$1" "$3"; else printf 'FAIL  %-58s expected %s, got %s\n' "$1" "$2" "$3"; FAILED=1; fi
}
status() { curl -s -o /dev/null -m 15 -w '%{http_code}' "$@" || echo "000"; }

if [ "$#" -eq 2 ]; then
  app_dns="$1"
  go_dns="$2"
else
  require_eks_cluster
  step "Argo CD applications"
  ek -n argocd get applications
  not_ok="$(ek -n argocd get applications -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.status.sync.status}{"/"}{.status.health.status}{"\n"}{end}' | grep -v 'Synced/Healthy' || true)"
  check "all Argo CD applications are Synced/Healthy" "" "$not_ok"
  step "workloads"
  ek -n linkforge get pods,hpa,ingress
  app_dns="$(ek -n linkforge get ingress app -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
  go_dns="$(ek -n linkforge get ingress go -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
fi

step "HTTP checks against the two ALBs (client-side: from wherever this runs)"
check "two different load balancers" "different" "$([ "$app_dns" != "$go_dns" ] && echo different || echo same)"
check "app: GET /" "200" "$(status "http://$app_dns/")"
check "app: GET /api/v1/auth/me (no session)" "401" "$(status "http://$app_dns/api/v1/auth/me")"
check "go: unknown short code" "404" "$(status "http://$go_dns/zzzzzzz")"
check "go: /api/v1/auth/me is hidden by the host guard" "404" "$(status "http://$go_dns/api/v1/auth/me")"
check "go: / is not the web app" "404" "$(status "http://$go_dns/")"
check "go: POST is refused on the public host" "404" "$(status -X POST "http://$go_dns/api/v1/auth/login")"

echo
if [ "$FAILED" = 0 ]; then echo "ALL CHECKS PASSED"; else echo "SOME CHECKS FAILED"; exit 1; fi
