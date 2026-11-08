#!/usr/bin/env bash
# eks-fix-webhook-ca.sh: make the AWS Load Balancer Controller's admission webhooks trust the certificate it really serves.
# YOU run it (bootstrap-eks.sh and eks-seal-and-enable.sh already call it; running it by hand is safe at any time):
#
#   export KUBECONFIG=~/.kube/linkforge-eks.yaml
#   scripts/eks-fix-webhook-ca.sh 2>&1 | tee ~/runlogs/eks-fix-webhook-ca.log
#
# THE PROBLEM (found on the first real run, D90): the chart makes a NEW random certificate authority on every render. The Argo CD
# application for the controller ignores differences in the TLS Secret and in the webhooks' `caBundle` (otherwise Argo would see a
# permanent diff), so it keeps whatever values were created first. If the first sync was applied in more than one attempt, the
# Secret (the certificate the controller serves) and the webhooks' caBundle (the authority Kubernetes trusts) can come from DIFFERENT
# renders. Then every Ingress is rejected with `x509: certificate signed by unknown authority` and the ALBs are never created.
# (That the mismatch comes from a retried first sync is a hypothesis; the mismatch itself was measured: two different hashes.)
#
# THE FIX: copy the CA from the Secret `aws-load-balancer-tls` (`ca.crt`, public, the one that signed the served certificate) into
# the `caBundle` of every webhook in the two webhook configurations. It only edits webhooks whose caBundle differs, so a second run
# changes nothing, and Argo CD leaves the field alone because it is in the application's ignoreDifferences.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/eks-common.sh"

step "AWS Load Balancer Controller webhooks: does the CA they trust match the certificate the controller serves?"
require_eks_cluster
CA="$(ek -n kube-system get secret aws-load-balancer-tls -o jsonpath='{.data.ca\.crt}' 2> /dev/null || true)"
[ -n "$CA" ] || die "the Secret aws-load-balancer-tls (key ca.crt) was not found in kube-system: is the aws-load-balancer-controller application installed and Healthy?"

patched=0
checked=0
for kind in validatingwebhookconfiguration mutatingwebhookconfiguration; do
  names="$(ek get "$kind" aws-load-balancer-webhook -o jsonpath='{.webhooks[*].name}' 2> /dev/null || true)"
  [ -n "$names" ] || die "$kind aws-load-balancer-webhook has no webhooks (or does not exist)"
  i=0
  for name in $names; do
    live="$(ek get "$kind" aws-load-balancer-webhook -o jsonpath="{.webhooks[$i].clientConfig.caBundle}")"
    checked=$((checked + 1))
    if [ "$live" != "$CA" ]; then
      ek patch "$kind" aws-load-balancer-webhook --type=json \
        -p "[{\"op\":\"replace\",\"path\":\"/webhooks/$i/clientConfig/caBundle\",\"value\":\"$CA\"}]" > /dev/null
      echo "patched: $kind / $name"
      patched=$((patched + 1))
    fi
    i=$((i + 1))
  done
done

if [ "$patched" = 0 ]; then
  echo "all $checked webhooks already trust the controller's CA: nothing to do"
else
  echo "fixed $patched of $checked webhooks (the CA now matches the Secret)"
fi
