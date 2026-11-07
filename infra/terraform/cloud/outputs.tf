output "region" {
  description = "AWS region of everything in this stack."
  value       = var.region
}

output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint (reachable only from admin_cidrs)."
  value       = module.eks.cluster_endpoint
}

output "kubeconfig_command" {
  description = "Run this in YOUR shell to get a kubeconfig in its own file (never merged into ~/.kube/config, so no tool of mine can see the cloud context)."
  value       = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name} --kubeconfig ~/.kube/linkforge-eks.yaml"
}

output "cluster_addons" {
  description = "The add-ons and the versions EKS installed."
  value       = module.eks.cluster_addons
}

output "vpc_id" {
  description = "VPC id."
  value       = module.vpc.vpc_id
}

output "db_endpoint" {
  description = "Database host name; DATABASE_URL points at it and verify-full checks the certificate against it."
  value       = module.rds.address
}

output "db_port" {
  description = "Database port."
  value       = module.rds.port
}

output "db_name" {
  description = "Database name."
  value       = module.rds.db_name
}

output "db_username" {
  description = "Database user."
  value       = module.rds.username
}

output "db_password" {
  description = "Database password. NEVER print it: use `terraform output -raw db_password | scripts/...` (P9.3)."
  value       = module.rds.password
  sensitive   = true
}

output "acm_certificate_arn" {
  description = "Domain mode only: the ACM certificate for the Ingress annotation. null in HTTP mode."
  value       = try(module.dns[0].certificate_arn, null)
}

output "domain_hosts" {
  description = "Domain mode only: the two host names. null in HTTP mode."
  value       = try({ app = module.dns[0].app_host, go = module.dns[0].go_host }, null)
}
