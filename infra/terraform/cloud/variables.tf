variable "region" {
  description = "AWS region. This account can only use one region (D76): Sydney."
  type        = string
  default     = "ap-southeast-2"
}

variable "allowed_account_id" {
  description = "The 12-digit AWS account this stack may touch (the provider refuses any other). No default: you type it in terraform.tfvars (git-ignored)."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be exactly 12 digits."
  }
}

variable "name" {
  description = "Name of the cluster and prefix of everything this stack creates (IAM roles, security groups, the database)."
  type        = string
  default     = "linkforge"
}

# ---- network -------------------------------------------------------------------------------------
variable "vpc_cidr" {
  description = "Address range of the VPC. Pods get addresses from it too (VPC CNI), so it must be roomy; a /16 is."
  type        = string
  default     = "10.20.0.0/16"
}

variable "az_count" {
  description = "How many availability zones to spread over. At least 2: an RDS subnet group and the ALBs both need two."
  type        = number
  default     = 2

  validation {
    condition     = var.az_count >= 2 && var.az_count <= 3
    error_message = "az_count must be 2 or 3."
  }
}

variable "single_nat_gateway" {
  description = "One NAT gateway for the whole VPC (cheap, about one third of a month of cost per gateway) instead of one per zone. If its zone fails, nodes lose outbound internet. Fine for a disposable demo."
  type        = bool
  default     = true
}

# ---- cluster -------------------------------------------------------------------------------------
variable "kubernetes_version" {
  description = "EKS Kubernetes version. Default 1.36: in standard support until 2027-08-02 (docs.aws.amazon.com/eks, checked 2026-11-07), i.e. past the credit expiry (D3), so no extended-support surcharge. kind runs 1.35; the manifests use nothing newer."
  type        = string
  default     = "1.36"
}

variable "admin_cidrs" {
  description = "Who may reach the PUBLIC Kubernetes API endpoint: your IP as a /32 (update it when your IP changes). No default on purpose: an open endpoint must be written down explicitly."
  type        = list(string)

  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for c in var.admin_cidrs : can(cidrhost(c, 0))])
    error_message = "admin_cidrs must be a non-empty list of valid CIDR blocks, for example [\"203.0.113.7/32\"]."
  }
}

variable "node_instance_types" {
  description = <<-EOT
    EC2 instance type(s) of the managed node group. Candidates (P9 plan):
      ["m7i-flex.large"]  2 vCPU / 8 GiB; on AWS's Free-plan EC2 list; pod capacity must be confirmed with
                          `aws ec2 describe-instance-types` (max pods = ENIs x (IPs per ENI - 1) + 2)
      ["t3.medium"]       2 vCPU / 4 GiB; about 17 pods per node, so use 3 nodes; NOT on the Free-plan EC2 list
    Check eligibility and pod capacity BEFORE applying (README, "Before the first apply").
  EOT
  type        = list(string)
  default     = ["m7i-flex.large"]
}

variable "node_min_size" {
  description = "Smallest node count."
  type        = number
  default     = 2
}

variable "node_desired_size" {
  description = "Node count at creation. Terraform ignores later changes to it (the group may be resized by hand)."
  type        = number
  default     = 2

  validation {
    condition     = var.node_min_size <= var.node_desired_size && var.node_desired_size <= var.node_max_size
    error_message = "Node counts must satisfy node_min_size <= node_desired_size <= node_max_size."
  }
}

variable "node_max_size" {
  description = "Largest node count."
  type        = number
  default     = 3

  validation {
    condition     = var.node_max_size >= 1
    error_message = "node_max_size must be at least 1."
  }
}

variable "node_disk_size_gb" {
  description = "Root volume of each node (gp3, encrypted)."
  type        = number
  default     = 30
}

# ---- database ------------------------------------------------------------------------------------
variable "db_instance_class" {
  description = "RDS instance class. db.t4g.micro is the smallest Graviton class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "Database storage in GB (gp3)."
  type        = number
  default     = 20
}

variable "db_engine_version" {
  description = "PostgreSQL version. Major only: RDS picks its current default minor, which keeps the apply from failing on a minor version that was retired."
  type        = string
  default     = "16"
}

# ---- optional: a real domain ---------------------------------------------------------------------
variable "domain_name" {
  description = <<-EOT
    A domain you already own whose Route53 hosted zone ALREADY EXISTS in this account (this stack never
    creates a zone and never buys a domain). Empty (the default) = HTTP mode: no ACM certificate, no DNS
    records, the apps are reached by the two AWS-generated ALB names (README, "Two modes").
  EOT
  type        = string
  default     = ""
}

variable "app_subdomain" {
  description = "Domain mode only: the web app lives at <app_subdomain>.<domain_name>."
  type        = string
  default     = "app"
}

variable "go_subdomain" {
  description = "Domain mode only: short links live at <go_subdomain>.<domain_name>."
  type        = string
  default     = "go"
}

variable "alb_dns_names" {
  description = "Domain mode only, SECOND apply: the DNS names of the two ALBs the load balancer controller created, as {app = \"...\", go = \"...\"}. Terraform then adds the alias records. Leave empty on the first apply (the ALBs do not exist yet)."
  type        = map(string)
  default     = {}
}
