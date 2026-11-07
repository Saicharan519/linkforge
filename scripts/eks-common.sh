# Shared by the EKS scripts (sourced, not run). Written for the HUMAN to run; Claude never runs anything that uses it
# against a real cluster.
#
# The guard: these scripts change a CLOUD cluster, so they refuse to run unless kubectl is clearly pointed at one,
# through a kubeconfig file of its own. The file comes from `terraform output -raw kubeconfig_command | bash` and is
# never merged into ~/.kube/config, so no other tool and no other terminal sees the cloud context by accident.

die() { echo "ERROR: $*" >&2; exit 1; }
step() { printf '\n[%s] === %s\n' "$(date +%H:%M:%S)" "$*"; }

EKS_KUBECONFIG="${KUBECONFIG:-}"

# Every kubectl call names the kubeconfig explicitly: nothing here depends on the current context of ~/.kube/config.
ek() { kubectl --kubeconfig "$EKS_KUBECONFIG" "$@"; }

require_eks_cluster() {
  command -v kubectl > /dev/null || die "kubectl is missing"
  [ -n "$EKS_KUBECONFIG" ] || die "KUBECONFIG is not set. Create the EKS kubeconfig (terraform output -raw kubeconfig_command | bash) and: export KUBECONFIG=~/.kube/linkforge-eks.yaml"
  [ -f "$EKS_KUBECONFIG" ] || die "kubeconfig file not found: $EKS_KUBECONFIG"
  case "$EKS_KUBECONFIG" in
    */.kube/config) die "refusing the default kubeconfig ($EKS_KUBECONFIG): use the separate EKS file" ;;
  esac
  local ctx server provider
  ctx="$(ek config current-context 2> /dev/null)" || die "no current context in $EKS_KUBECONFIG"
  case "$ctx" in kind-*) die "context '$ctx' is a kind cluster, not EKS" ;; esac
  server="$(ek config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
  case "$server" in
    https://*.eks.amazonaws.com) ;;
    *) die "the API server '$server' is not an EKS endpoint (https://<id>.<x>.<region>.eks.amazonaws.com)" ;;
  esac
  provider="$(ek get nodes -o jsonpath='{.items[0].spec.providerID}' 2> /dev/null)" \
    || die "cannot list nodes: is your IP in admin_cidrs, and is the cluster up?"
  case "$provider" in
    aws:///*) ;;
    *) die "the first node's providerID '$provider' is not an AWS instance" ;;
  esac
  echo "cluster: $server (context $ctx, nodes on AWS)"
}
