# PostgreSQL on RDS for LinkForge: private, encrypted, TLS required, reachable only from the cluster nodes.
#
# This is a DISPOSABLE demo database (ARCHITECTURE §10). The settings that would be wrong for production are
# marked below with what production would do instead.

# The master password. Generated here so nobody ever types or sees it. It lives in the Terraform state (private,
# encrypted bucket) and is handed to Kubernetes as a SealedSecret by a script that pipes
# `terraform output -raw db_password` into kubeseal; it never appears in a file or in chat.
# Letters and digits only: the password goes inside a postgresql:// URL, where characters such as @ : / ? #
# would need escaping.
resource "random_password" "master" {
  length  = 32
  special = false
}

# Who may connect: only the cluster's nodes (pods use the node security group), on the PostgreSQL port.
resource "aws_security_group" "db" {
  name        = "${var.name}-db"
  description = "PostgreSQL from the Kubernetes nodes only"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "from_nodes" {
  security_group_id            = aws_security_group.db.id
  description                  = "PostgreSQL from the Kubernetes nodes"
  referenced_security_group_id = var.node_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# `rds.force_ssl = 1`: the SERVER refuses an unencrypted connection, in addition to our clients insisting on
# verify-full (D14, D85). Two locks on one door.
resource "aws_db_parameter_group" "this" {
  name_prefix = "${var.name}-pg16-"
  family      = "postgres16"
  description = "LinkForge: require TLS"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  #checkov:skip=CKV_AWS_293:DISPOSABLE demo database: terraform destroy must be able to delete it (production: deletion_protection = true)
  #checkov:skip=CKV_AWS_157:single zone to halve the cost (production: multi_az = true)
  #checkov:skip=CKV_AWS_118:enhanced monitoring needs an extra IAM role and costs money; CloudWatch basic metrics are enough for a demo
  #checkov:skip=CKV_AWS_353:performance insights are not needed for a demo database of this size
  #checkov:skip=CKV_AWS_129:no log exports to CloudWatch (cost); postgres logs stay on the instance
  #checkov:skip=CKV_AWS_161:the app authenticates with the master password over verify-full TLS (IAM database authentication would need token handling in the app, not in v1)
  identifier = var.name

  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage_gb
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = var.db_name
  username = var.username
  password = random_password.master.result

  db_subnet_group_name   = var.db_subnet_group_name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false

  # Single zone (a standby in a second zone doubles the cost). Production: multi_az = true.
  multi_az = false

  # The CA that signs the server certificate; clients verify against the RDS bundle baked into the image.
  ca_cert_identifier = var.ca_cert_identifier

  # DISPOSABLE: one day of automatic backups, no final snapshot, no deletion protection, so that
  # `terraform destroy` really removes everything and nothing keeps billing afterwards. Production:
  # a retention of 7+ days, `skip_final_snapshot = false` with a snapshot identifier, and
  # `deletion_protection = true`.
  backup_retention_period    = 1
  skip_final_snapshot        = true
  deletion_protection        = false
  delete_automated_backups   = true
  copy_tags_to_snapshot      = true
  auto_minor_version_upgrade = true

  # Changes (for example a bigger class) apply at once instead of waiting for the maintenance window.
  apply_immediately = true
}
