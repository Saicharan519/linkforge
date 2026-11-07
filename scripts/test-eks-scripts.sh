#!/usr/bin/env bash
# Offline test of the EKS scripts (P9.3): no AWS, no cluster, no network. Fake `kubectl`, `terraform` and `kubeseal` programs
# (shims) stand in for the real ones, and the gitops repo is a throw-away clone with a local bare repository as "origin".
#   scripts/test-eks-scripts.sh            (uses ~/linkforge-gitops as the source of the clone; GITOPS_SRC overrides)
# What it proves: the cluster guard refuses wrong targets; the sealing script reads the database settings, seals through
# pipes, never lets the password reach a file or the log, enables the Application in one commit and refuses to run twice;
# the host script refuses one shared load balancer and bad names and edits exactly three values. What it CANNOT prove:
# anything about real EKS, real kubeseal output, real Terraform output or a real cluster.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GITOPS_SRC="${GITOPS_SRC:-$HOME/linkforge-gitops}"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
PASS=0
FAIL=0
ok() { PASS=$((PASS + 1)); printf 'PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL  %s\n' "$1"; }
expect_fail() { # expect_fail "name" "text the error must contain" cmd...
  local name="$1" needle="$2"
  shift 2
  local out
  if out="$("$@" 2>&1)"; then bad "$name (it should have failed)"; else
    if grep -qF -- "$needle" <<< "$out"; then ok "$name"; else bad "$name (failed, but without '$needle': $(tail -1 <<< "$out"))"; fi
  fi
}

# ---- shims ------------------------------------------------------------------------------------------------------
mkdir -p "$T/shim"
cat > "$T/shim/kubectl" << 'EOF'
#!/usr/bin/env bash
# fake kubectl: first two arguments are "--kubeconfig FILE"
shift 2
all="$*"
case "$all" in
  "config current-context") echo "${FAKE_CONTEXT:-arn:aws:eks:ap-southeast-2:123456789012:cluster/linkforge}" ;;
  "config view --minify -o jsonpath={.clusters[0].cluster.server}") printf '%s' "${FAKE_SERVER:-https://ABCDEF.gr7.ap-southeast-2.eks.amazonaws.com}" ;;
  "get nodes -o jsonpath={.items[0].spec.providerID}") printf '%s' "${FAKE_PROVIDER:-aws:///ap-southeast-2a/i-0abc}" ;;
  "-n linkforge get ingress app -o jsonpath={.status.loadBalancer.ingress[0].hostname}") printf '%s' "${FAKE_APP_DNS:-}" ;;
  "-n linkforge get ingress go -o jsonpath={.status.loadBalancer.ingress[0].hostname}") printf '%s' "${FAKE_GO_DNS:-}" ;;
  *) echo "(fake kubectl) $all" ;;
esac
EOF
cat > "$T/shim/terraform" << 'EOF'
#!/usr/bin/env bash
# fake terraform: `terraform -chdir=DIR output -raw NAME`
name="${@: -1}"
case "$name" in
  db_endpoint) printf 'linkforge.abc123.ap-southeast-2.rds.amazonaws.com' ;;
  db_port) printf '5432' ;;
  db_name) printf 'linkforge' ;;
  db_username) printf 'linkforge' ;;
  db_password) printf 'TestOnlyPassword0123456789abcdefgh' ;;
  *) exit 1 ;;
esac
EOF
cat > "$T/shim/kubeseal" << 'EOF'
#!/usr/bin/env bash
# fake kubeseal --raw: reads the secret from STDIN (like the real one), records its sha256 for the test, prints fake ciphertext
data="$(cat)"
h="$(printf '%s' "$data" | sha256sum | cut -c1-64)"
echo "$h $*" >> "${SEAL_LOG:?}"
printf 'AgFAKE%s' "${h:0:40}"
EOF
chmod +x "$T/shim"/*
export PATH="$T/shim:$PATH"
export SEAL_LOG="$T/seal.log"
export KUBECONFIG="$T/eks.yaml"
: > "$KUBECONFIG"

# ---- gitops clone with a local origin -------------------------------------------------------------------------------
git clone -q --bare "$GITOPS_SRC" "$T/origin.git"
git clone -q "$T/origin.git" "$T/gitops"
git -C "$T/gitops" config user.email test@example.test
git -C "$T/gitops" config user.name test
export GITOPS_DIR="$T/gitops"

echo "== guard (eks-common.sh)"
unset KUBECONFIG
expect_fail "refuses when KUBECONFIG is not set" "KUBECONFIG is not set" "$HERE/bootstrap-eks.sh"
export KUBECONFIG="$T/eks.yaml"
FAKE_SERVER="https://127.0.0.1:6443" expect_fail "refuses a non-EKS API endpoint (kind)" "not an EKS endpoint" "$HERE/bootstrap-eks.sh"
FAKE_CONTEXT="kind-linkforge" expect_fail "refuses a kind context" "is a kind cluster" "$HERE/bootstrap-eks.sh"
FAKE_PROVIDER="kind://docker/linkforge/linkforge-control-plane" expect_fail "refuses nodes that are not AWS instances" "not an AWS instance" "$HERE/bootstrap-eks.sh"
mkdir -p "$T/home/.kube"
cp "$KUBECONFIG" "$T/home/.kube/config"
KUBECONFIG="$T/home/.kube/config" expect_fail "refuses the default ~/.kube/config" "refusing the default kubeconfig" "$HERE/bootstrap-eks.sh"

echo "== eks-seal-and-enable.sh"
expect_fail "refuses while the IP placeholder is still in values.yaml" "REPLACE_" "$HERE/eks-seal-and-enable.sh" --no-push
sed -i 's|REPLACE_WITH_YOUR_IP/32|203.0.113.7/32|' "$T/gitops/envs/eks/values.yaml"
git -C "$T/gitops" commit -qam "test: set the CIDR"
LOG="$T/seal-run.log"
if "$HERE/eks-seal-and-enable.sh" --no-push > "$LOG" 2>&1; then ok "runs to the end with --no-push"; else bad "seal-and-enable failed: $(tail -3 "$LOG")"; fi
cd "$T/gitops"
[ -f envs/eks/sealed.values.yaml ] && ok "writes envs/eks/sealed.values.yaml" || bad "no sealed.values.yaml"
[ -f argocd/eks/apps/linkforge.yaml ] && [ ! -e argocd/eks/pending/linkforge.yaml ] && ok "moves the Application from pending/ to apps/" || bad "Application not moved"
git log -1 --name-status --format=%s | grep -q "enable the linkforge Application" && ok "one commit enables everything" || bad "unexpected commit"
git show --stat --format= HEAD | grep -q "sealed.values.yaml" && git show --stat --format= HEAD | grep -q "linkforge.yaml" && ok "the commit holds both the sealed values and the move" || bad "commit content wrong"
if grep -rq "TestOnlyPassword" "$T/gitops" "$LOG" "$T/seal.log" 2> /dev/null; then bad "THE PASSWORD LEAKED into the repo, the log or the seal arguments"; else ok "the password appears nowhere (repo, log, kubeseal arguments)"; fi
expected_url_hash="$(printf '%s' 'postgresql+asyncpg://linkforge:TestOnlyPassword0123456789abcdefgh@linkforge.abc123.ap-southeast-2.rds.amazonaws.com:5432/linkforge' | sha256sum | cut -c1-64)"
grep -q "^$expected_url_hash " "$T/seal.log" && ok "kubeseal received exactly the expected DATABASE_URL on stdin" || bad "kubeseal did not receive the expected URL"
grep -q -- "--scope strict --name linkforge-db --namespace linkforge" "$T/seal.log" && ok "sealed with strict scope for linkforge-db / linkforge" || bad "wrong kubeseal scope arguments"
python3 - << 'EOF' && ok "sealed.values.yaml has exactly DATABASE_URL and IP_HASH_SECRET as ciphertext" || bad "sealed.values.yaml has the wrong shape"
import yaml
d = yaml.safe_load(open("envs/eks/sealed.values.yaml"))
e = d["secrets"]["sealed"]["encryptedData"]
assert d["secrets"]["sealed"]["enabled"] is True and sorted(e) == ["DATABASE_URL", "IP_HASH_SECRET"]
assert all(v.startswith("AgFAKE") for v in e.values())
EOF
expect_fail "refuses to run a second time" "already enabled" "$HERE/eks-seal-and-enable.sh" --no-push
if helm template linkforge charts/linkforge -n linkforge -f envs/eks/values.yaml -f envs/eks/sealed.values.yaml > "$T/render.yaml" 2> "$T/render.err"; then ok "the chart renders with the generated sealed values"; else bad "chart render failed: $(tail -2 "$T/render.err")"; fi
cd "$HERE"

echo "== eks-set-hosts.sh"
APP=k8s-linkforge-app-1111111111-2222222222.ap-southeast-2.elb.amazonaws.com
GO=k8s-linkforge-go-3333333333-4444444444.ap-southeast-2.elb.amazonaws.com
expect_fail "refuses ONE shared load balancer" "two-ALB assumption" "$HERE/eks-set-hosts.sh" --no-push "$APP" "$APP"
expect_fail "refuses a name that is not an ALB name" "does not look like an ALB DNS name" "$HERE/eks-set-hosts.sh" --no-push "example.com" "$GO"
FAKE_APP_DNS="" FAKE_GO_DNS="" expect_fail "refuses when an Ingress has no address yet" "no address yet" "$HERE/eks-set-hosts.sh" --no-push
before="$(git -C "$T/gitops" rev-parse HEAD)"
FAKE_APP_DNS="$APP" FAKE_GO_DNS="$GO" "$HERE/eks-set-hosts.sh" --no-push > "$T/hosts.log" 2>&1 && ok "reads both names from the (fake) cluster and commits" || bad "set-hosts failed: $(tail -3 "$T/hosts.log")"
v="$T/gitops/envs/eks/values.yaml"
grep -q "^  SHORT_URL_BASE: http://$GO\$" "$v" && grep -q "^  PUBLIC_REDIRECT_HOST: $GO\$" "$v" && grep -q "^  ALLOWED_ORIGINS: http://$APP\$" "$v" && ok "wrote SHORT_URL_BASE, PUBLIC_REDIRECT_HOST and ALLOWED_ORIGINS" || bad "values.yaml not updated correctly"
[ "$(git -C "$T/gitops" diff --numstat "$before" HEAD | awk '{a+=$1; d+=$2} END{print a"/"d}')" = "3/3" ] && ok "changed exactly three lines" || bad "more than three lines changed"
grep -v "^[[:space:]]*#" "$v" | grep -q "pending.invalid" && bad "pending.invalid is still in a value of values.yaml" || ok "no pending.invalid left in any value"
helm template linkforge "$T/gitops/charts/linkforge" -n linkforge -f "$v" -f "$T/gitops/envs/eks/sealed.values.yaml" > /dev/null 2>&1 && ok "the chart still renders with the real hosts" || bad "chart render failed after set-hosts"

echo "== syntax"
for f in eks-common.sh bootstrap-eks.sh eks-seal-and-enable.sh eks-set-hosts.sh eks-verify.sh test-eks-scripts.sh; do
  bash -n "$HERE/$f" && ok "bash -n $f" || bad "syntax error in $f"
done
if command -v shellcheck > /dev/null; then
  shellcheck -S warning -x "$HERE"/eks-*.sh "$HERE"/bootstrap-eks.sh > "$T/shellcheck.log" 2>&1 && ok "shellcheck (warnings and above)" || { bad "shellcheck"; head -20 "$T/shellcheck.log"; }
else
  echo "(shellcheck not installed: skipped)"
fi

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
