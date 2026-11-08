#!/usr/bin/env bash
# Drill 2 (P10.2): kill Redis while k6 sends 100 redirects/s; the volume is an EBS gp3 disk. Run on the EKS cluster only.
set -uo pipefail
case "${KUBECONFIG:-}" in
  */linkforge-eks.yaml) ;;
  *) echo "KUBECONFIG is not the EKS file: refusing"; exit 1 ;;
esac
cd ~/linkforge
ts() { date +%H:%M:%S; }
rx() { kubectl -n linkforge exec redis-0 -- redis-cli "$@" 2> /dev/null; }
entries_read() { rx XINFO GROUPS clicks | awk '/^entries-read$/{getline; print; exit}'; }
state() { echo "stream length: $(rx XLEN clicks)  entries-read: $(entries_read)  lag/pending: $(rx XINFO GROUPS clicks | awk '/^lag$/{getline; l=$0} /^pending$/{getline; p=$0} END{print l "/" p}')"; }

echo "[$(ts)] BEFORE (waiting 10 s so the previous test has drained)"; sleep 10
state
ER0="$(entries_read)"
echo "redis-0 node: $(kubectl -n linkforge get pod redis-0 -o jsonpath='{.spec.nodeName}')"

kubectl -n k6 delete job k6-load --ignore-not-found
sed -e 's/__ONLY__/redirect_warm/' -e 's/WARM_RATE, value: "200"/WARM_RATE, value: "100"/' infra/k6/k6-job-eks.yaml | kubectl apply -f - > /dev/null
echo "[$(ts)] load started; waiting until k6 reports 'running'"
for i in $(seq 1 45); do
  if kubectl -n k6 logs job/k6-load 2> /dev/null | grep -q '^running ('; then break; fi
  sleep 2
done
echo "[$(ts)] load is running; waiting 30 s, then killing Redis"
sleep 30
T0=$(date +%s)
echo "[$(ts)] DELETING redis-0"
kubectl -n linkforge delete pod redis-0 --wait=false
for i in $(seq 1 90); do
  ready="$(kubectl -n linkforge get pod redis-0 -o jsonpath='{.status.containerStatuses[0].ready}' 2> /dev/null)"
  printf '[%s] +%ss redis-0 ready=%s\n' "$(ts)" "$(( $(date +%s) - T0 ))" "${ready:-no-pod}"
  if [ "$ready" = "true" ]; then break; fi
  sleep 3
done
echo "redis-0 node now: $(kubectl -n linkforge get pod redis-0 -o jsonpath='{.spec.nodeName}' 2> /dev/null)"
state
echo "[$(ts)] volume events for redis-0 (attach/mount):"
kubectl -n linkforge get events --field-selector involvedObject.name=redis-0 --sort-by=.lastTimestamp 2> /dev/null | tail -8

echo "[$(ts)] waiting for k6 to finish"
kubectl -n k6 wait --for=condition=complete job/k6-load --timeout=300s
kubectl -n k6 logs job/k6-load | sed -n '/THRESHOLDS/,$p'
echo "[$(ts)] AFTER (waiting 20 s for the worker to drain)"; sleep 20
state
ER1="$(entries_read)"
echo "clicks sent by k6: 18001   clicks the worker read from Redis: $((ER1 - ER0))"
