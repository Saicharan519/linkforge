variable "region" {
  description = "AWS region for the state bucket. This account can only use one region (D76): Sydney."
  type        = string
  default     = "ap-southeast-2"
}

variable "allowed_account_id" {
  description = <<-EOT
    The 12-digit AWS account this stack may touch. The provider refuses to run against any other
    account, so a wrong AWS_PROFILE can never create a bucket somewhere unexpected. No default on
    purpose: you type it into terraform.tfvars (git-ignored), so it is not committed.
  EOT
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be exactly 12 digits."
  }
}

variable "bucket_prefix" {
  description = "State bucket name = <prefix>-<account id>. Bucket names are global across all of AWS, the account id makes the name unique to you."
  type        = string
  default     = "linkforge-tfstate"
}

variable "noncurrent_version_retention_days" {
  description = "How long OLD versions of a state file are kept (versioning keeps every change). 90 days is enough to roll back a bad apply, and old versions cost storage."
  type        = number
  default     = 90
}
