# Which Terraform, which providers, where the state lives, and what every resource is tagged with.
#
# `required_version` is a floor (the S3 lock file used below needs 1.11+). Providers are pinned with
# `~>`: bug fixes arrive, a breaking major version never does; the exact build that was used is
# recorded in .terraform.lock.hcl (committed), so everyone, and CI, gets the same one.
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.72"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # State lives in the bucket that bootstrap/ created. The bucket name contains the account id, which
  # we keep out of the repository, so the rest of the backend settings come from backend.hcl:
  #   terraform init -backend-config=backend.hcl        (see ../backend.hcl.example and README.md)
  # One state file per stack: this one is cloud/terraform.tfstate. It holds the database password
  # (random_password), which is why the bucket is private, versioned and encrypted.
  backend "s3" {
    key = "cloud/terraform.tfstate"
  }
}

provider "aws" {
  region = var.region

  # A wrong AWS_PROFILE can never build this stack in some other account.
  allowed_account_ids = [var.allowed_account_id]

  # Every taggable resource this stack creates gets these (infra/CLAUDE.md). `Env=prod` is the label of
  # the cloud environment (as opposed to kind), even though it is a disposable demo.
  default_tags {
    tags = {
      Project   = "linkforge"
      Env       = "prod"
      ManagedBy = "terraform"
    }
  }
}
