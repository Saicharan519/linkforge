# OPTIONAL domain mode (own module: modules/dns). With `domain_name = ""` (the default) NOTHING here is created:
# the demo runs in HTTP mode on the two AWS-generated ALB names (README, "Two modes").

module "dns" {
  source = "../modules/dns"
  count  = var.domain_name != "" ? 1 : 0

  domain_name   = var.domain_name
  app_subdomain = var.app_subdomain
  go_subdomain  = var.go_subdomain
  alb_dns_names = var.alb_dns_names
}
