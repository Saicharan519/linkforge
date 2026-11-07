output "address" {
  description = "Host name of the database (what goes after the @ in DATABASE_URL, and what verify-full checks the certificate against)."
  value       = aws_db_instance.this.address
}

output "port" {
  description = "PostgreSQL port."
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Database name."
  value       = aws_db_instance.this.db_name
}

output "username" {
  description = "Master user name."
  value       = aws_db_instance.this.username
}

output "password" {
  description = "Master password (sensitive). Read it only through `terraform output -raw db_password` into a pipe."
  value       = random_password.master.result
  sensitive   = true
}

output "security_group_id" {
  description = "Security group of the database."
  value       = aws_security_group.db.id
}
