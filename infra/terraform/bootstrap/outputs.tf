output "state_bucket" {
  description = "Name of the state bucket. Put it in backend.hcl for the other stacks (see README.md)."
  value       = aws_s3_bucket.state.id
}

output "region" {
  description = "Region of the state bucket."
  value       = var.region
}
