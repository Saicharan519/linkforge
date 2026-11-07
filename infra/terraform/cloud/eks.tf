# The Kubernetes cluster: the managed control plane, a managed node group, and the add-ons.
#
# Two decisions that follow from this account (D76, P9 plan):
#   - NO IAM OIDC provider (enable_irsa = false). The account's managed policy denies iam:*Provider*, and
#     the classic way for a pod to get AWS permissions (IRSA) needs one. EKS Pod Identity does not: the
#     `eks-pod-identity-agent` add-on runs on every node and hands a pod the credentials of the role it is
#     associated with (see iam.tf).
#   - The API endpoint is public but only for `admin_cidrs` (your IP). Nodes reach it privately.

module "eks" {
  #checkov:skip=CKV_TF_1:registry modules cannot be pinned by commit; the version is pinned EXACTLY (immutable on the registry) and reviewed on every bump
  source  = "terraform-aws-modules/eks/aws"
  version = "21.29.0"

  name               = var.name
  kubernetes_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.admin_cidrs
  endpoint_private_access      = true

  enable_irsa = false

  # Whoever runs `terraform apply` (the IAM user) becomes a cluster administrator through an EKS access
  # entry, so the same person can run kubectl. Nobody else gets in unless an access entry is added.
  enable_cluster_creator_admin_permissions = true

  # Disposable demo: no customer-managed KMS key and no control-plane log group (both cost money and need
  # extra permissions). EKS still encrypts the cluster's etcd at rest with an AWS-owned key. A production
  # cluster would set a KMS key for secrets encryption and enable the `audit` and `api` logs.
  create_kms_key              = false
  encryption_config           = null
  enabled_log_types           = []
  create_cloudwatch_log_group = false

  # Add-ons. `most_recent = false` with no `addon_version`: EKS installs ITS default version for this
  # Kubernetes version, which is always compatible with it (a "latest" could be newer than the cluster
  # supports). The versions that were really installed are shown by `terraform output cluster_addons`.
  addons = {
    # vpc-cni and the Pod Identity agent must exist BEFORE the nodes start (nodes need the CNI to get
    # pod addresses; the agent must run on them).
    vpc-cni = {
      before_compute = true
      most_recent    = false
    }
    eks-pod-identity-agent = {
      before_compute = true
      most_recent    = false
    }
    kube-proxy = {
      most_recent = false
    }
    coredns = {
      most_recent = false
    }
    # Needed for the PersistentVolumeClaim of Redis (AOF on an EBS gp3 volume). Its controller gets AWS
    # permissions through Pod Identity (the role is in iam.tf).
    aws-ebs-csi-driver = {
      most_recent = false
      pod_identity_association = [{
        role_arn        = aws_iam_role.ebs_csi.arn
        service_account = "ebs-csi-controller-sa"
      }]
    }
  }

  eks_managed_node_groups = {
    main = {
      instance_types = var.node_instance_types
      capacity_type  = "ON_DEMAND"
      ami_type       = "AL2023_x86_64_STANDARD"

      # The role the nodes run as. Named linkforge-node-<suffix> (the module default would start with the
      # node group key, "main-"), so the IAM policy for the person running terraform can be limited to roles
      # called linkforge-* (iam-policy-p9.json).
      iam_role_name = "${var.name}-node"

      min_size     = var.node_min_size
      max_size     = var.node_max_size
      desired_size = var.node_desired_size

      # IMDSv2 only (a stolen-request attack cannot read the node credentials with a plain GET), but with a
      # hop limit of 2 so that PODS (one network hop further) can still read the instance metadata. The load
      # balancer controller uses it to discover the region and the VPC id without us passing them in.
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 2
      }

      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = var.node_disk_size_gb
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }
    }
  }
}
