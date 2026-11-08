# cloud: VPC, EKS, RDS (P9.1)

Everything the demo needs on AWS, in Sydney (`ap-southeast-2`, the only region this account can use, D76).
**Claude writes this code and never applies it.** You run every command below in your own shell and `tee` the
output to `~/runlogs/` so it can be read afterwards. Destroyable at any time without touching `bootstrap/`.

## What it creates

| File | Resources |
|---|---|
| `network.tf` | VPC, public / private / database subnets in 2 zones, internet gateway, ONE NAT gateway |
| `eks.tf` | EKS control plane (Kubernetes 1.36), one managed node group, add-ons: vpc-cni, kube-proxy, coredns, `eks-pod-identity-agent`, `aws-ebs-csi-driver`. **No IAM OIDC provider** (D76): Pod Identity instead of IRSA |
| `iam.tf` | Roles for the EBS CSI driver and the AWS Load Balancer Controller (Pod Identity); the controller's official policy is vendored in `policies/` |
| `database.tf` -> `../modules/rds` | RDS PostgreSQL 16 (db.t4g.micro, private, encrypted, `rds.force_ssl=1`), a generated password, a security group that admits only the nodes |
| `dns.tf` -> `../modules/dns` | **Optional**, only when `domain_name` is set: ACM certificate + validation records (+ alias records on a second apply) in a hosted zone that ALREADY exists |

NOT created here (on purpose): the ALBs (the load balancer controller makes them from our two Ingresses), any Kubernetes
object, the SealedSecret with the database URL, a Route53 zone, a domain.

## Two modes

- **HTTP mode (default, `domain_name = ""`)**: no certificate, no DNS. Each of the two Ingresses becomes its own ALB, reachable at its
  own AWS-generated name (`k8s-...elb.ap-southeast-2.amazonaws.com`). The short-link host is the second ALB's name. **There is no TLS:**
  passwords and session cookies cross the internet in clear text, so use test accounts only and keep the ALBs' allowed CIDRs to your IP.
- **Domain mode (`domain_name = "example.com"`)**: HTTPS on `app.<domain>` and `go.<domain>`. Apply once (certificate), let Argo CD create
  the ALBs, then apply again with `alb_dns_names = { app = "...", go = "..." }` to add the alias records.

## Before the first apply (read-only checks and permissions)

1. **Budget alert works and you know your credit balance** (D3: expires 2027-04-28; EKS bills from the minute the cluster exists).
2. **Permissions.** `linkforge-terraform` has only the S3 state policy (bootstrap). Add `iam-policy-p9.json` as a **customer managed policy** attached to the user (inline policies are capped at 2048 characters in total per user and this file exceeds it;
   managed ones allow 6144) (put your account
   id where it says `<ACCOUNT_ID>`; drop the last statement if you stay in HTTP mode). It is scoped: EC2/EKS/RDS only in `ap-southeast-2`, IAM
   only for roles, policies and instance profiles named `linkforge-*`, `AttachRolePolicy` only for the managed policies this stack uses.
   Narrow it again after `terraform destroy`. **It may still not be enough or may be overridden**: the account's AWS-managed policy (D76) can
   contain explicit denies, and an explicit deny always wins. The first `plan`/`apply` tells you; each `AccessDenied` names the action.
3. **Instance type and pod capacity** (read-only; needs `ec2:Describe*` and `servicequotas:Get*`):
   ```bash
   aws ec2 describe-instance-type-offerings --region ap-southeast-2 --filters Name=instance-type,Values=t3.medium,m7i-flex.large --query 'InstanceTypeOfferings[].InstanceType'
   aws ec2 describe-instance-types --region ap-southeast-2 --instance-types t3.medium m7i-flex.large --query 'InstanceTypes[].[InstanceType,VCpuInfo.DefaultVCpus,MemoryInfo.SizeInMiB,NetworkInfo.MaximumNetworkInterfaces,NetworkInfo.Ipv4AddressesPerInterface]' --output text
   aws service-quotas get-service-quota --region ap-southeast-2 --service-code ec2 --quota-code L-1216C47A --query 'Quota.Value'
   ```
   Max pods per node = ENIs x (IPs per ENI - 1) + 2 (t3.medium: 17). Count about 4 pod slots per node for DaemonSets. Computed in
   `linkforge-gitops/envs/eks/README.md` (D87): 24 pods at rest and about 29 at the HPA maximum with monitoring off (+4 for lean monitoring), so `2 x t3.medium` (34 slots) barely fits and
   `3 x t3.medium` has room; the earlier estimate of 32-37 assumed full monitoring. The vCPU quota
   must cover nodes x 2. AWS's Free-plan EC2 list (accounts from 2025-07-15) is `t3.micro`, `t3.small`, `t4g.micro`, `t4g.small`,
   `c7i-flex.large`, `m7i-flex.large`: `t3.medium` is NOT on it, which is why the default is `m7i-flex.large`. Whether this account really
   may launch a type is only known when the node group is created.
4. `cp terraform.tfvars.example terraform.tfvars` and fill in the account id and **your IP /32** (`curl -s https://checkip.amazonaws.com`).
5. `cp ../backend.hcl.example backend.hcl`, put your account id in the bucket name.

## Run it

```bash
cd ~/linkforge/infra/terraform/cloud
export AWS_PROFILE=linkforge                       # this shell only
terraform init -backend-config=backend.hcl 2>&1 | tee ~/runlogs/cloud-init.log
terraform plan -out=tfplan 2>&1 | tee ~/runlogs/cloud-plan.log     # READ it; never commit or paste tfplan
terraform apply tfplan 2>&1 | tee ~/runlogs/cloud-apply.log
```

**First time on this account, find out what is allowed before paying for the expensive parts.** Apply in stages; every stage can be undone:
```bash
terraform apply -target=module.vpc            # free except the NAT gateway (hourly)
terraform apply -target=module.eks            # starts the EKS clock: roughly 15 minutes
terraform apply                               # the rest (database, roles, associations)
```
If a stage fails with `AccessDenied` / `not authorized` / `not available on the Free plan`, stop, run `terraform destroy` for what exists, and
report the log: that is the answer to "does this account allow it". The plan for the fallback (k3s on one EC2, D2) is in DEVOPS_GUIDE §14.

Then get a kubeconfig **in its own file** (the command is an output) so no other tool sees the cloud context:
```bash
terraform output -raw kubeconfig_command | bash
export KUBECONFIG=~/.kube/linkforge-eks.yaml        # only in the shell where YOU use kubectl on EKS
```

## Cost drivers (rough, from DEVOPS_GUIDE §14; check the AWS pricing pages for ap-southeast-2)
EKS control plane (about $0.10/h, billed until the cluster is deleted), the NAT gateway (hourly + per GB), the nodes, two ALBs in HTTP mode (one in domain
mode), RDS, EBS volumes and public IPv4 addresses. A forgotten environment is the real cost: calendar reminder for the destroy.

## Destroy (in this order; step by step in `docs/RUNBOOK-eks-destroy.md`; steps 1 and 2 are done by `scripts/eks-teardown.sh`)
1. Delete the Argo CD applications first (cascade): that removes the ALBs, their target groups and security groups, and the Redis PersistentVolumeClaim
   (and with it the EBS volume). If you `terraform destroy` first, orphaned ALBs / ENIs / volumes block the VPC deletion and keep billing.
2. `terraform destroy` in this directory.
3. Console checklist: EKS, EC2 instances, EBS volumes and snapshots, load balancers and target groups, NAT gateways, Elastic IPs, ENIs and security groups
   left in the VPC, RDS instances and snapshots, Route53 records (domain mode).

## Notes
- State: `cloud/terraform.tfstate` in the bucket from `bootstrap/`. It contains the database password.
- `terraform output -raw db_password` is meant to be piped into a sealing script (P9.3), never shown or pasted.
- Kubernetes add-on versions are the ones EKS picks for the cluster version (shown by `terraform output cluster_addons`); registry modules are pinned to an exact version.
