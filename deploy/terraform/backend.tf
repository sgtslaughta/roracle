# Default: local state (terraform.tfstate next to this file). State holds SENSITIVE values (secrets); protect it.
# Uncomment ONE example for shared state. Details: README "State backend".

# S3-compatible (e.g. the site MinIO, NOT the in-cluster tenant this stack deploys: chicken and egg).
# terraform {
#   backend "s3" {
#     bucket                      = "tfstate"
#     key                         = "roracle/terraform.tfstate"
#     region                      = "us-east-1" # ignored by MinIO, required by the backend
#     endpoints                   = { s3 = "https://minio.MIRROR.example.com" }
#     use_path_style              = true
#     skip_credentials_validation = true
#     skip_requesting_account_id  = true
#     skip_region_validation      = true
#     skip_metadata_api_check     = true
#     use_lockfile                = true # S3-native locking (Terraform >= 1.10); no DynamoDB
#     # credentials: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars
#   }
# }

# Kubernetes Secret backend (state in a Secret of an existing cluster/namespace; ~1 MiB limit, lease-locked).
# terraform {
#   backend "kubernetes" {
#     secret_suffix = "roracle"
#     namespace     = "tfstate" # must exist; create it out of band
#     config_path   = "~/.kube/config"
#   }
# }
