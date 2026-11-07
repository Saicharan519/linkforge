variable "domain_name" {
  description = "A domain whose PUBLIC Route53 hosted zone already exists in this account."
  type        = string
}

variable "app_subdomain" {
  description = "The web app is served at <app_subdomain>.<domain_name>."
  type        = string
}

variable "go_subdomain" {
  description = "Short links are served at <go_subdomain>.<domain_name>."
  type        = string
}

variable "alb_dns_names" {
  description = "DNS names of the two ALBs as {app = ..., go = ...} (second apply). Keys must be app and go."
  type        = map(string)
  default     = {}

  validation {
    condition     = alltrue([for k in keys(var.alb_dns_names) : contains(["app", "go"], k)])
    error_message = "alb_dns_names may only have the keys app and go."
  }
}
