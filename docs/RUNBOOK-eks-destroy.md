# Runbook: destroy the EKS demo (P9.4)

EKS has no pause button. The control plane bills until the cluster is deleted, and a few things created by controllers INSIDE the
cluster (the two ALBs, the Redis EBS volume) are invisible to Terraform. Follow this order. Set a calendar reminder when you apply.

## Before you start
- You have the separate EKS kubeconfig: `export KUBECONFIG=~/.kube/linkforge-eks.yaml` (your own shell; Claude never has it).
- Save what you want to keep: screenshots, the numbers in `docs/RESULTS.md`, `~/runlogs/*.log`. After the destroy the database and Redis are gone.
- `export AWS_PROFILE=linkforge` for the Terraform step and the read-only checks below.

## 1. Kubernetes side (script)
```bash
scripts/eks-teardown.sh 2>&1 | tee ~/runlogs/eks-teardown.log
```
It asks you to type the cluster's API host name, then:
1. deletes the `linkforge` Application (cascade): the two Ingresses go. The load balancer controller deletes each ALB (and its target group and security
   group) BEFORE it lets the Ingress disappear, so "no Ingress left" means "the ALBs are being or have been deleted";
2. deletes the namespace `linkforge`: removes the Redis claim, and with it the EBS volume (the StorageClass reclaim policy is `Delete`);
3. checks that no Ingress, no Service of type LoadBalancer, no PersistentVolumeClaim and no PersistentVolume is left, and refuses to say "clean" otherwise.

If it stops because something is left: do NOT run `terraform destroy`. Look at `kubectl -n kube-system logs deploy/aws-load-balancer-controller`
(permissions? Pod Identity?). A stuck Ingress finalizer can be removed by hand only after you have checked in the console that the load balancer is really gone.
If you enabled the optional monitoring app nothing extra is needed (its storage is `emptyDir`).

## 2. Terraform side (you)
```bash
cd ~/linkforge/infra/terraform/cloud
terraform destroy 2>&1 | tee ~/runlogs/cloud-destroy.log      # READ the plan of deletions; it takes about 10-15 minutes
```
If it fails with `DependencyViolation` on a subnet or the VPC: something still uses a network interface or security group in it. Almost always an ALB
or its security group that step 1 did not finish. Find it (checks below), remove it, run `terraform destroy` again (it is safe to repeat).

## 3. Check that nothing billable is left (read-only AWS commands, in YOUR shell, region ap-southeast-2)
```bash
aws eks list-clusters --region ap-southeast-2
aws elbv2 describe-load-balancers --region ap-southeast-2 --query 'LoadBalancers[].[LoadBalancerName,State.Code]' --output text
aws ec2 describe-volumes --region ap-southeast-2 --filters Name=status,Values=available,in-use --query 'Volumes[].[VolumeId,Size,State,Tags[?Key==`kubernetes.io/created-for/pvc/name`]|[0].Value]' --output text
aws ec2 describe-nat-gateways --region ap-southeast-2 --filter Name=state,Values=available,pending --query 'NatGateways[].NatGatewayId' --output text
aws ec2 describe-addresses --region ap-southeast-2 --query 'Addresses[].[PublicIp,AssociationId]' --output text
aws rds describe-db-instances --region ap-southeast-2 --query 'DBInstances[].DBInstanceIdentifier' --output text
aws rds describe-db-snapshots --region ap-southeast-2 --query 'DBSnapshots[].DBSnapshotIdentifier' --output text
```
Expected: nothing from the stack (your EC2 instance `orderintel` belongs to you and stays). Anything else tagged `Project=linkforge` is a leftover.

## 4. Console checklist (ap-southeast-2) (the same list as DEVOPS_GUIDE §14, with this stack's names)
EKS clusters · EC2 instances and the node group's Auto Scaling group · EBS volumes and snapshots · Load Balancers and Target Groups · NAT gateways ·
**Elastic IPs** · network interfaces and security groups left in the VPC `linkforge` · RDS instances, **snapshots** and retained automated backups ·
CloudWatch log groups · IAM roles/policies named `linkforge-*` (Terraform removes them; check) · Route53 records and an ACM certificate (domain mode only).
A domain registration is a separate purchase: deleting a zone does not cancel it.

## 5. After the destroy
- Narrow the IAM policy of `linkforge-terraform` back to the state-bucket-only policy (`infra/terraform/bootstrap/README.md`) and set its access key
  **inactive** (or delete it) in the IAM console.
- Delete `~/.kube/linkforge-eks.yaml` (it points at a cluster that no longer exists) and unset `KUBECONFIG`.
- The state bucket (`linkforge-tfstate-<account>`) stays: it costs next to nothing and holds the state history.
- **Next day:** Billing, Cost Explorer, daily cost: the stack's services should be about zero. If something still bills, it is in step 3 or 4.
- Record in `docs/TASKS.md`: what was destroyed, what the checks printed, what the credit balance is now.

## What Claude can and cannot do here
Claude writes and reviews these steps and reads logs you paste or `tee`; it never runs `terraform destroy`, `aws`, or `kubectl` against the cluster
(`.claude/settings.json` denies them, and the EKS kubeconfig exists only in your shell).
