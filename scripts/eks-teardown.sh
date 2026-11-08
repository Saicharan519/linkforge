#!/usr/bin/env bash
# eks-teardown.sh: the KUBERNETES half of the EKS destroy (P9.4). YOU run it BEFORE `terraform destroy`:
#
#   export KUBECONFIG=~/.kube/linkforge-eks.yaml
#   scripts/eks-teardown.sh 2>&1 | tee ~/runlogs/eks-teardown.log
#
# Why an order exists: the two ALBs, their target groups and security groups, and the Redis EBS volume are created by
# controllers INSIDE the cluster, not by Terraform. `terraform destroy` knows nothing about them: run it first and the
# cluster disappears with those AWS resources still there, billing, and their network interfaces block the deletion of
# the VPC. So, in this order:
#   0 switch off the auto-sync of the Argo CD `root` Application. Found on the first real teardown (D90): the root has the
#     `linkforge` Application in git and self-heals, so it re-created the Application, its namespace, the Redis claim and
#     the ALBs seconds after they were deleted, and the final check stopped at "STILL THERE". (This is step 2/5 below.)
#   1 delete the `linkforge` Application (cascade): its two Ingresses go; the load balancer controller keeps a finalizer on
#     each Ingress and removes it only after the ALB is deleted, so "Ingress gone" means "ALB gone"
#   2 delete the namespace `linkforge`: this removes the Redis PersistentVolumeClaim (Argo's cascade does not: StatefulSet
#     claims are not tracked) and, because the StorageClass reclaim policy is Delete, the EBS volume behind it. The EBS CSI driver
#     must still be running for that, which is why this happens before Terraform removes the add-on
#   3 verify that no Ingress, no LoadBalancer Service and no volume claim is left
# It does NOT run terraform and does NOT use any AWS command: it prints what to run next.
#
# Asks for a typed confirmation (the cluster's API host name); `--yes` skips it (used by the offline test).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/eks-common.sh"

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --yes) ASSUME_YES=1 ;;
    *) die "unknown argument: $arg" ;;
  esac
done
TIMEOUT="${TEARDOWN_TIMEOUT:-600s}"

step "1/5 is kubectl pointed at the EKS cluster?"
require_eks_cluster
host="$(ek config view --minify -o jsonpath='{.clusters[0].cluster.server}' | sed 's|^https://||; s|\..*||')"
if [ "$ASSUME_YES" = 0 ]; then
  echo "This deletes the linkforge application, both load balancers and the Redis volume of the cluster '$host'."
  read -r -p "Type the first part of the API host name ($host) to continue: " answer
  [ "$answer" = "$host" ] || die "not confirmed; nothing was deleted"
fi

step "2/5 switch off auto-sync of the root Application (otherwise it re-creates everything we delete next)"
if ek -n argocd get application root > /dev/null 2>&1; then
  ek -n argocd patch application root --type merge -p '{"spec":{"syncPolicy":null}}'
else
  echo "no root Application (already gone, or never installed)"
fi

step "3/5 delete the linkforge Application (cascade: Ingresses, so ALBs)"
if ek -n argocd get application linkforge > /dev/null 2>&1; then
  ek -n argocd delete application linkforge --wait=true --timeout="$TIMEOUT"
else
  echo "no linkforge Application (already gone, or never enabled)"
fi
echo "waiting until no Ingress is left in namespace linkforge (the controller deletes the ALBs first)..."
ek -n linkforge wait --for=delete ingress --all --timeout="$TIMEOUT" 2> /dev/null || true
left="$(ek -n linkforge get ingress -o name 2> /dev/null || true)"
[ -z "$left" ] || die "Ingresses still exist ($left): the ALBs may not be deleted yet. Look at the load balancer controller logs, do NOT run terraform destroy yet."

step "4/5 delete the namespace linkforge (Redis claim and EBS volume)"
if ek get namespace linkforge > /dev/null 2>&1; then
  ek delete namespace linkforge --wait=true --timeout="$TIMEOUT"
else
  echo "no namespace linkforge"
fi

step "5/5 verify nothing that costs money is left inside the cluster"
problems=0
# the root was switched off above; if the Application or the namespace is back anyway, something re-created them (stop: the ALBs may be back too)
if ek -n argocd get application linkforge > /dev/null 2>&1; then echo "STILL THERE (the Application linkforge was re-created)"; problems=1; fi
if ek get namespace linkforge > /dev/null 2>&1; then echo "STILL THERE (the namespace linkforge was re-created)"; problems=1; fi
ingresses="$(ek get ingress -A -o name 2> /dev/null || true)"
if [ -n "$ingresses" ]; then echo "STILL THERE (Ingress): $ingresses"; problems=1; fi
lbs="$(ek get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}/{.metadata.name}{"\n"}{end}' 2> /dev/null || true)"
if [ -n "$lbs" ]; then echo "STILL THERE (Service of type LoadBalancer): $lbs"; problems=1; fi
pvcs="$(ek get pvc -A -o name 2> /dev/null || true)"
if [ -n "$pvcs" ]; then echo "STILL THERE (PersistentVolumeClaim): $pvcs"; problems=1; fi
pvs="$(ek get pv -o name 2> /dev/null || true)"
if [ -n "$pvs" ]; then echo "STILL THERE (PersistentVolume, so maybe an EBS volume): $pvs"; problems=1; fi
[ "$problems" = 0 ] || die "something is still left, see above. Fix it (or wait and rerun) BEFORE terraform destroy."

echo
echo "The cluster is clean. Next, in YOUR shell:"
echo "  cd ~/linkforge/infra/terraform/cloud && terraform destroy 2>&1 | tee ~/runlogs/cloud-destroy.log"
echo "Then the console checklist: docs/RUNBOOK-eks-run-day.md"
