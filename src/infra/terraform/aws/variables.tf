variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "project" {
  description = "Project name prefix"
  type        = string
  default     = "agentic"
}

variable "vpc_cidr" {
  type        = string
  default     = "10.20.0.0/16"
}

variable "allowed_cidr" {
  description = "CIDR allowed to access DB/Redis (restrict in prod!)"
  type        = string
  default     = "0.0.0.0/0"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_username" {
  type    = string
  default = "postgres"
}

variable "db_password" {
  type      = string
  default   = "change-me"
  sensitive = true
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "s3_bucket_name" {
  description = "Name for S3 bucket to store blobs"
  type        = string
  default     = null
}
