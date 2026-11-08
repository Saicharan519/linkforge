#!/usr/bin/env bash
# Drill 1 (P10.2): kill one API pod while k6 sends 100 redirects/s. Run on the EKS cluster only.
set -uo pipefail
case "${KUBECONFIG:-}" in
  */linkforge-eks.yaml) ;;
  *) echo "KUBECONFIG is not the EKS file: refusing"; exit 1 ;;
esac
cd ~/linkforge
ts() { date +%H:%M:%S; }
kubectl -n k6 delete job k6-load --ignore-not-found
sed -e 's/__ONLY__/redirect_warm/' -e 's/WARM_RATE, value: "200"/WARM_RATE, value: "100"/' infra/k6/k6-job-eks.yaml | kubectl apply -f - > /dev/null
echo "[$(ts)] load started; waiting until k6 reports 'running'"
for i in $(seq 1 45); do
  if kubectl -n k6 logs job/k6-load 2> /dev/null | grep -q '^running ('; then break; fi
  sleep 2
done
echo "[$(ts)] load is running; waiting 30 s, then killing one API pod"
sleep 30
echo "api pods before:"
kubectl -n linkforge get pods -l app=api --no-headers
victim="$(kubectl -n linkforge get pods -l app=api -o name | head -1)"
echo "[$(ts)] DELETING $victim"
kubectl -n linkforge delete "$victim" --wait=false
for i in $(seq 1 20); do
  echo "[$(ts)] ready/desired: $(kubectl -n linkforge get deploy api -o jsonpath='{.status.readyReplicas}/{.spec.replicas}')"
  sleep 2
done
echo "api pods after:"
kubectl -n linkforge get pods -l app=api --no-headers
echo "[$(ts)] waiting for k6 to finish"
kubectl -n k6 wait --for=condition=complete job/k6-load --timeout=300s
kubectl -n k6 logs job/k6-load | sed -n '/THRESHOLDS/,$p'
