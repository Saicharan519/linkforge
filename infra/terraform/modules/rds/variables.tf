variable "name" {
  description = "Name prefix and database instance identifier."
  type        = string
}

variable "vpc_id" {
  description = "VPC the database lives in."
  type        = string
}

variable "db_subnet_group_name" {
  description = "Subnet group (private database subnets in at least two zones)."
  type        = string
}

variable "node_security_group_id" {
  description = "Security group of the Kubernetes nodes: the only source allowed to reach PostgreSQL."
  type        = string
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
}

variable "allocated_storage_gb" {
  description = "Storage in GB."
  type        = number
}

variable "engine_version" {
  description = "PostgreSQL version (major only: RDS picks its current minor)."
  type        = string
}

variable "db_name" {
  description = "Name of the database the application uses."
  type        = string
  default     = "linkforge"
}

variable "username" {
  description = "Master user name."
  type        = string
  default     = "linkforge"
}

variable "ca_cert_identifier" {
  description = "RDS server certificate authority. rds-ca-rsa2048-g1 is in the global bundle that backend/certs/ vendors."
  type        = string
  default     = "rds-ca-rsa2048-g1"
}
