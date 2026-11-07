output "certificate_arn" {
  description = "ARN of the validated ACM certificate (goes into the Ingress annotation alb.ingress.kubernetes.io/certificate-arn)."
  value       = aws_acm_certificate_validation.this.certificate_arn
}

output "app_host" {
  description = "Host name of the web app."
  value       = local.app_host
}

output "go_host" {
  description = "Host name of the short links."
  value       = local.go_host
}
