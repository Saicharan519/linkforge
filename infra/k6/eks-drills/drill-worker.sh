#!/usr/bin/env bash
# Drill 3 (P10.2): worker down via git (worker.replicas: 0), 1 minute of 100 redirects/s, then restore and time the drain.
# Run on the EKS cluster only. It makes two commits in ~/linkforge-gitops (the change and its revert) and pushes them.
set -uo pipefail
case "${KUBECONFIG:-}" in
  */linkforge-eks.yaml) ;;
  *) echo "KUBECONFIG is not the EKS file: refusing"; exit 1 ;;
esac
ts() { date +%H:%M:%S; }
rx() { kubectl -n linkforge exec redis-0 -- redis-cli "$@" 2> /dev/null; }
entries_read() { rx XINFO GROUPS clicks | awk '/^entries-read$/{getline; print; exit}'; }
lag() { rx XINFO GROUPS clicks | awk '/^lag$/{getline; print; exit}'; }
pend() { rx XINFO GROUPS clicks | awk '/^pending$/{getline; print; exit}'; }
state() { echo "stream length: $(rx XLEN clicks)  entries-read: $(entries_read)  lag: $(lag)  pending: $(pend)"; }
refresh() { kubectl -n argocd annotate app linkforge argocd.argoproj.io/refresh=hard --overwrite > /dev/null; }

cd ~/linkforge-gitops
[ -z "$(git status --porcelain)" ] || { echo "gitops repo is not clean: stop"; exit 1; }
git pull --rebase -q
echo "[$(ts)] BEFORE"; state
ER0="$(entries_read)"

echo "[$(ts)] committing worker.replicas: 0"
sed -i '/^worker:/a\  replicas: 0' envs/eks/values.yaml
git diff --stat
git commit -q -am "drill: stop the worker (worker.replicas 0), reverted right after"
git push -q origin main
refresh
echo "[$(ts)] waiting for the worker pod to go away"
for i in $(seq 1 60); do
  n="$(kubectl -n linkforge get pods -l app=worker --no-headers 2> /dev/null | wc -l)"
  [ "$n" = "0" ] && break
  refresh
  sleep 5
done
echo "[$(ts)] worker pods: $n"

cd ~/linkforge
kubectl -n k6 delete job k6-load --ignore-not-found
sed -e 's/__ONLY__/redirect_warm/' -e 's/WARM_RATE, value: "200"/WARM_RATE, value: "100"/' -e 's/DURATION, value: "3m"/DURATION, value: "1m"/' infra/k6/k6-job-eks.yaml | kubectl apply -f - > /dev/null
echo "[$(ts)] load started (1 minute, no worker)"
kubectl -n k6 wait --for=condition=complete job/k6-load --timeout=300s
kubectl -n k6 logs job/k6-load | sed -n '/THRESHOLDS/,$p' | grep -E "✓|✗|http_req_failed|iterations|http_req_duration|rate_limited"
SENT="$(kubectl -n k6 logs job/k6-load | grep -E '^ +iterations' | awk '{print $2}')"
echo "[$(ts)] WITH THE WORKER DOWN:"; state
echo "iterations sent by k6: $SENT"

cd ~/linkforge-gitops
echo "[$(ts)] reverting: worker comes back"
git revert --no-edit HEAD > /dev/null
git push -q origin main
refresh
T0=""
for i in $(seq 1 90); do
  r="$(kubectl -n linkforge get deploy worker -o jsonpath='{.status.readyReplicas}' 2> /dev/null)"
  if [ "$r" = "1" ]; then T0=$(date +%s); break; fi
  refresh
  sleep 5
done
echo "[$(ts)] worker ready"
for i in $(seq 1 60); do
  l="$(lag)"; p="$(pend)"
  printf '[%s] +%ss lag=%s pending=%s\n' "$(ts)" "$(( $(date +%s) - T0 ))" "$l" "$p"
  if [ "$l" = "0" ] && [ "$p" = "0" ]; then break; fi
  sleep 5
done
echo "[$(ts)] AFTER"; state
ER1="$(entries_read)"
echo "clicks sent: $SENT   clicks the worker read from Redis: $((ER1 - ER0))"
