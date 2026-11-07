# bootstrap/: the S3 bucket that holds the Terraform state of every other stack (P6.2).
#
# What is "state"? Terraform's record of which real AWS objects belong to which block in the code.
# Without it Terraform would not know what it already built. The state can contain secrets (for
# example the database password in P9), so the bucket that holds it must be private, encrypted and
# versioned (a bad apply can be rolled back by restoring the previous version of the file).
#
# Run once by a human, with local state. Commands and the reasoning: README.md.

provider "aws" {
  region = var.region

  # Safety net: abort if the credentials belong to a different account than the one in tfvars.
  allowed_account_ids = [var.allowed_account_id]

  # Every resource created by this provider gets these tags (infra/CLAUDE.md). Tags are how you find
  # and price what belongs to the project in Cost Explorer.
  default_tags {
    tags = {
      Project   = "linkforge"
      Env       = "prod"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  bucket_name = "${var.bucket_prefix}-${data.aws_caller_identity.current.account_id}"
}

# ---- The bucket ------------------------------------------------------------------------------
resource "aws_s3_bucket" "state" {
  bucket = local.bucket_name

  # Never let `terraform destroy` (or a typo) delete the place where the state of everything lives.
  # To remove it on purpose, delete this block's protection first, in a reviewed change.
  force_destroy = false
  lifecycle {
    prevent_destroy = true
  }

  # Checkov findings accepted on purpose, each with its reason:
  #checkov:skip=CKV_AWS_18:Access logging needs a second bucket to hold the logs; a one-person project does not need an audit trail of its own state bucket.
  #checkov:skip=CKV_AWS_144:Cross-region replication doubles the cost and this account can only use one region anyway (D76); versioning is the recovery mechanism.
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256, free) is used instead of a customer-managed KMS key (about 1 USD a month); the bucket is private and TLS-only.
  #checkov:skip=CKV2_AWS_62:Event notifications are for pipelines reacting to uploads; nothing here does.
}

# Keep every version of every object: the state file's history.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Encrypt everything at rest. AES256 = SSE-S3, keys managed by S3 itself at no cost.
# bucket_key_enabled cuts the number of key requests (cheaper, no downside here).
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# The four switches that make public access impossible, whatever anyone configures later.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Switch off the legacy ACL permission system: the bucket owner owns every object and only IAM
# policies decide who can do what.
resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Refuse any request that does not use HTTPS: state could otherwise be read in transit.
data "aws_iam_policy_document" "tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.tls_only.json

  # Attach a policy only after the public-access block exists: S3 rejects policy changes that race
  # with it ("conflicting conditional operation").
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# Housekeeping so the bucket does not grow forever.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    # An empty filter = the whole bucket (the provider requires one of filter/prefix).
    filter {}

    # Old versions of a state file are deleted after N days; the CURRENT version never expires.
    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_version_retention_days
    }

    # A half-finished upload (a crashed `terraform apply`) leaves invisible parts that are billed.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Versioning must be on before a rule about non-current versions makes sense.
  depends_on = [aws_s3_bucket_versioning.state]
}
