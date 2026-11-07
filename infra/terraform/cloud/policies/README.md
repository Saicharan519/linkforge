# Vendored IAM policies

| File | What | Source | sha256 |
|---|---|---|---|
| `aws-load-balancer-controller-v3.6.2.json` | The IAM policy of the AWS Load Balancer Controller v3.6.2 (16 statements) | https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v3.6.2/docs/install/iam_policy.json (downloaded 2026-11-07) | `16f232c9d9f79366fe949c4550ad517a202380058a9e48d45a4e215044a20a6a` |

Committed instead of fetched during `terraform apply`: the apply is reproducible, the policy that gets attached is
reviewed in a pull request, and a changed file upstream cannot silently widen it.

**Keep it in step with the controller version** that Argo CD installs (P9.2, chart pinned): when that version changes,
download the matching `iam_policy.json` for the same tag, update this table, open a pull request.
