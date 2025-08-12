variable "region"        { type = string, default = "eu-west-1" }
variable "project"       { type = string, default = "agentic" }
variable "nat_gateway_single" { type = bool, default = true }

variable "eks_version"   { type = string, default = "1.29" }
variable "eks_min_size"  { type = number, default = 2 }
variable "eks_max_size"  { type = number, default = 6 }
variable "eks_desired"   { type = number, default = 3 }
variable "node_instance_types" { type = list(string), default = ["t3.large"] }

variable "db_instance_class" { type = string, default = "db.t4g.small" }
variable "db_name"       { type = string, default = "agentic" }
variable "db_username"   { type = string, default = "postgres" }
variable "db_password"   { type = string, sensitive = true, default = "change-me" }

variable "redis_node_type" { type = string, default = "cache.t4g.small" }
variable "s3_bucket_name"  { type = string, default = null }
