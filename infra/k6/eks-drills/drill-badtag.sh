#!/usr/bin/env bash
# Drill 4 (P10.2): a bad frontend image tag via git, watch the rolling update protect the app, roll back with git revert.
# Run on the EKS cluster only. Two commits in ~/linkforge-gitops (change + revert).
set -uo pipefail
case "${KUBECONFIG:-}" in
  */linkforge-eks.yaml) ;;
  *) echo "KUBECONFIG is not the EKS file: refusing"; exit 1 ;;
esac
ts() { date +%H:%M:%S; }
refresh() { kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite > /dev/null; }
APP="$(kubectl -n linkforge get ingress app -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')"
probe() { curl -s -o /dev/null -m 10 -w '%{http_code}' "http://$APP/" || echo 000; }
show() { echo "[$(ts)] app GET / = $(probe)   frontend pods:"; kubectl -n linkforge get pods -l app=frontend --no-headers | sed 's/^/    /'; }

cd ~/linkforge-gitops
[ -z "$(git status --porcelain)" ] || { echo "gitops repo is not clean: stop"; exit 1; }
git pull --rebase -q
echo "[$(ts)] BEFORE"; show
python3 - << 'EOF'
import re
p = 'envs/eks/values.yaml'
t = open(p).read()
t2 = re.sub(r'(?m)^(frontend:\n  image:\n    repository: [^\n]+\n    tag: )"[^"]+"', r'\1"sha-doesnotexist"', t)
assert t != t2, "frontend tag line not found"
open(p, 'w').write(t2)
EOF
git diff --stat
git commit -q -am "drill: bad frontend image tag (reverted right after)"
git push -q origin main
refresh
for i in $(seq 1 12); do
  sleep 10
  show
done
echo "[$(ts)] deployment status:"
kubectl -n linkforge get deploy frontend
kubectl -n linkforge get pods -l app=frontend -o jsonpath='{range .items[*]}{.metadata.name}{"  "}{.status.containerStatuses[0].state.waiting.reason}{"\n"}{end}'

echo "[$(ts)] ROLLING BACK: git revert"
git revert --no-edit HEAD > /dev/null
git push -q origin main
refresh
T0=$(date +%s)
kubectl -n linkforge rollout status deploy/frontend --timeout=300s
echo "rollback finished after $(( $(date +%s) - T0 )) s"
show
echo "[$(ts)] Argo: $(kubectl -n argocd get app linkforge -o jsonpath='{.status.sync.status}/{.status.health.status}')"
