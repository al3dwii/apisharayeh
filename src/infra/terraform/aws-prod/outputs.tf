output "cluster_name"               { value = module.eks.cluster_name }
output "cluster_endpoint"           { value = module.eks.cluster_endpoint }
output "cluster_security_group_id"  { value = module.eks.cluster_security_group_id }
output "oidc_provider_arn"          { value = module.eks.oidc_provider_arn }

output "database_url" {
  value     = "postgresql+asyncpg://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.address}:${aws_db_instance.postgres.port}/${var.db_name}"
  sensitive = true
}

output "redis_url"  { value = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0" }
output "redis_url_queue"  { value = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/2" }
output "s3_bucket"  { value = aws_s3_bucket.blobs.bucket }
output "secret_name" { value = aws_secretsmanager_secret.app.name }
