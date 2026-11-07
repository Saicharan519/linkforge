# bootstrap: the Terraform state bucket (P6.2)

Creates ONE thing: a private, versioned, encrypted, TLS-only S3 bucket named
`linkforge-tfstate-<your account id>` in `ap-southeast-2` (Sydney, the only region this account can
use, D76). Every later Terraform stack stores its state in it.

Why a separate stack with local state? The bucket cannot hold the state of the stack that creates it
(chicken and egg), so this stack keeps its own small state file, `terraform.tfstate`, next to
these files. It is git-ignored (state can contain secrets). Keep the file; if you lose it, Terraform
forgets the bucket exists (the bucket itself is not harmed, it can be re-imported).

## What you need
- Terraform >= 1.11 (`terraform version`).
- An AWS CLI profile that may create and configure ONE S3 bucket (policy below). In this account:
  an IAM user with access keys, because the account has no console admin role or SSO yet.

## Least-privilege policy for the profile that runs this stack
Attach it to the IAM user instead of AdministratorAccess. Replace `<ACCOUNT_ID>`. It allows every S3
action, but only on the state bucket (plus listing buckets, which Terraform's provider does).

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "StateBucketOnly",
      "Effect": "Allow",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::linkforge-tfstate-<ACCOUNT_ID>",
        "arn:aws:s3:::linkforge-tfstate-<ACCOUNT_ID>/*"
      ]
    },
    {
      "Sid": "ListBuckets",
      "Effect": "Allow",
      "Action": "s3:ListAllMyBuckets",
      "Resource": "*"
    }
  ]
}
```
Later phases (P9: VPC, EKS, RDS, DNS) need much wider rights. Widen the policy for that phase, on
purpose and for as long as it is needed, then narrow it again.

## Run it (you, in your own terminal; Claude never runs these)

```bash
cd ~/linkforge/infra/terraform/bootstrap
export AWS_PROFILE=linkforge                      # this shell only
aws sts get-caller-identity                       # must show the account you expect
cp terraform.tfvars.example terraform.tfvars      # then edit: put your 12-digit account id in it

terraform init
terraform plan -out=tfplan                        # READ the plan: 7 to add, 0 to change, 0 to destroy
terraform apply tfplan
```
Expected plan: **7 to add** (the bucket, versioning, encryption, public-access block, ownership
controls, bucket policy and lifecycle rule) and nothing else. If the number differs, stop and ask.
Never commit or paste `tfplan` (it can contain values you did not intend to share).

After the apply, `terraform plan` must say **No changes**. That is the "done when".

## How the other stacks will use the bucket
The bucket name is not written into the repository (the account id stays out of git). Each later
stack takes it from a git-ignored `backend.hcl` (see `../backend.hcl.example`):

```bash
terraform init -backend-config=backend.hcl
```

## Remove it (only when the whole project is over)
`prevent_destroy` blocks `terraform destroy` on purpose. Delete all object versions first
(S3 console, "Show versions"), then remove that protection in a reviewed change.
