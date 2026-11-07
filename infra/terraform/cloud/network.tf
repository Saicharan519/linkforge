# The network: one VPC, three kinds of subnets in each of two zones.
#
#   public    the internet-facing load balancers (ALBs) and the NAT gateway
#   private   the Kubernetes nodes and pods: no public addresses, outbound internet only through the NAT
#             gateway (to pull images from GHCR and to talk to AWS APIs)
#   database  RDS, in its own subnets with no route to the internet at all
#
# NAT is the cost driver of this file: one gateway bills per hour plus per GB, so `single_nat_gateway`
# defaults to true (variable description).

data "aws_availability_zones" "available" {
  #checkov:skip=CKV_AWS_394:we take the first az_count zones by name and the subnets are created once per apply; a zone AWS adds later only matters to a new stack
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # /16 -> /20 slices: public at offsets 0.., private at 4.., database at 8.. (no overlap for up to 3 zones).
  public_subnets   = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  private_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 4)]
  database_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]
}

module "vpc" {
  #checkov:skip=CKV_TF_1:registry modules cannot be pinned by commit; the version is pinned EXACTLY (immutable on the registry) and reviewed on every bump
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.7.3"

  name = var.name
  cidr = var.vpc_cidr
  azs  = local.azs

  public_subnets   = local.public_subnets
  private_subnets  = local.private_subnets
  database_subnets = local.database_subnets

  # An RDS instance needs a subnet group; the module creates one from the database subnets.
  create_database_subnet_group = true

  enable_nat_gateway = true
  single_nat_gateway = var.single_nat_gateway

  # Pods and nodes resolve each other (and the RDS endpoint) by name.
  enable_dns_hostnames = true
  enable_dns_support   = true

  # The AWS Load Balancer Controller finds WHERE to put a load balancer by these subnet tags: `elb` =
  # internet-facing ones go to the public subnets, `internal-elb` = internal ones to the private subnets.
  # Without the tags the controller cannot create an ALB.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}
