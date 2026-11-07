# Which Terraform and which provider this configuration needs.
#
# `required_version` is a floor, not a pin: the S3 locking feature used by the other stacks
# (`use_lockfile`) exists from 1.11 on.
#
# The provider is pinned with `~>` (the pessimistic operator): `~> 6.72` accepts 6.72 up to (not
# including) 7.0. Bug fixes arrive automatically, a breaking major version never does. The EXACT
# version that was used is recorded in .terraform.lock.hcl, which is committed, so everyone (and CI)
# gets the same provider build.
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.72"
    }
  }

  # No `backend` block on purpose. This stack creates the bucket that stores everybody ELSE's
  # state, so it cannot store its own state there yet (chicken and egg): its small state file stays
  # on this machine, in terraform.tfstate next to these files (git-ignored: state can hold
  # secrets). Keep that file safe; see README.md.
}
