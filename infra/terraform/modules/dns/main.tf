# OPTIONAL domain mode: an ACM certificate for app.<domain> and go.<domain>, validated through DNS in a Route53
# hosted zone that ALREADY exists (this module never creates a zone and never buys a domain), plus alias
# records to the two ALBs once their names are known.
#
# The ALBs are created by the load balancer controller from our Ingresses, not by Terraform, so their names do
# not exist at the first apply. Hence two steps: apply once (certificate), let Argo CD create the ALBs, then
# apply again with `alb_dns_names` filled in (the alias records).

data "aws_route53_zone" "this" {
  name         = var.domain_name
  private_zone = false
}

locals {
  app_host = "${var.app_subdomain}.${var.domain_name}"
  go_host  = "${var.go_subdomain}.${var.domain_name}"
  hosts    = { app = local.app_host, go = local.go_host }
}

resource "aws_acm_certificate" "this" {
  domain_name               = local.app_host
  subject_alternative_names = [local.go_host]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }
}

# ACM proves we own the names by looking for these CNAME records; creating them is what "DNS-validated" means.
resource "aws_route53_record" "validation" {
  for_each = {
    for o in aws_acm_certificate.this.domain_validation_options : o.domain_name => {
      name   = o.resource_record_name
      type   = o.resource_record_type
      record = o.resource_record_value
    }
  }

  zone_id         = data.aws_route53_zone.this.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for r in aws_route53_record.validation : r.fqdn]
}

# Alias records app/go -> the ALBs (second apply only). An alias to an ALB needs the ALB's regional hosted zone id.
data "aws_lb_hosted_zone_id" "alb" {
  count = length(var.alb_dns_names) > 0 ? 1 : 0
}

resource "aws_route53_record" "alb_alias" {
  #checkov:skip=CKV2_AWS_23:the target is an ALB created by the load balancer controller, not by Terraform, so it is given by DNS name
  for_each = var.alb_dns_names

  zone_id = data.aws_route53_zone.this.zone_id
  name    = local.hosts[each.key]
  type    = "A"

  alias {
    name                   = each.value
    zone_id                = data.aws_lb_hosted_zone_id.alb[0].id
    evaluate_target_health = true
  }
}
