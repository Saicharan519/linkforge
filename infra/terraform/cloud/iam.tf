# AWS permissions for two things that run INSIDE the cluster, through EKS Pod Identity (no OIDC provider,
# see eks.tf):
#   - the EBS CSI driver (creates and attaches the gp3 volume behind Redis' PersistentVolumeClaim)
#   - the AWS Load Balancer Controller (turns our two Ingresses into two ALBs)
#
# How Pod Identity works: a role trusts the service `pods.eks.amazonaws.com`; an "association" says "pods
# of THIS service account in THIS namespace of THIS cluster may assume THAT role"; the agent on the node
# then gives such a pod temporary credentials. Nothing is stored anywhere.

data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

# ---- EBS CSI driver ---------------------------------------------------------------------------------
resource "aws_iam_role" "ebs_csi" {
  name               = "${var.name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

# AWS's own managed policy for exactly this driver.
resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

# (The association for this role is created by the add-on itself: see `pod_identity_association` in eks.tf.)

# ---- AWS Load Balancer Controller -------------------------------------------------------------------
resource "aws_iam_role" "lb_controller" {
  name               = "${var.name}-lb-controller"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

# The controller's official IAM policy, vendored (policies/README.md has the version, source and sha256):
# it must be able to create and manage load balancers, target groups, listeners and security groups, which
# is why it contains wildcard resources. Pinned to the controller version we install, never fetched live.
#checkov:skip=CKV_AWS_355:the upstream controller policy needs wildcard resources (it manages load balancers it has not created yet)
#checkov:skip=CKV_AWS_290:same upstream policy: write access without constraints is how the controller works
#checkov:skip=CKV_AWS_289:same upstream policy: permissions management is part of its security group handling
resource "aws_iam_policy" "lb_controller" {
  name   = "${var.name}-lb-controller"
  policy = file("${path.module}/policies/aws-load-balancer-controller-v3.6.2.json")
}

resource "aws_iam_role_policy_attachment" "lb_controller" {
  role       = aws_iam_role.lb_controller.name
  policy_arn = aws_iam_policy.lb_controller.arn
}

# "Pods of service account `aws-load-balancer-controller` in `kube-system` may assume the role". The names
# are the defaults of the controller's Helm chart; the Argo CD application (P9.2) must not change them.
resource "aws_eks_pod_identity_association" "lb_controller" {
  cluster_name    = module.eks.cluster_name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.lb_controller.arn
}
