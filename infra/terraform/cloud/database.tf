# The PostgreSQL database (own module: modules/rds). On EKS the chart runs with postgres.enabled=false and the
# API, the worker and the migration Job connect to this instance over TLS (verify-full, D14/D85).

module "rds" {
  source = "../modules/rds"

  name                   = var.name
  vpc_id                 = module.vpc.vpc_id
  db_subnet_group_name   = module.vpc.database_subnet_group_name
  node_security_group_id = module.eks.node_security_group_id

  instance_class       = var.db_instance_class
  allocated_storage_gb = var.db_allocated_storage_gb
  engine_version       = var.db_engine_version
}
