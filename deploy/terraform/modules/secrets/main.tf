# secrets_mode=create: every Secret the chart references, built from sensitive input. Values land in Terraform
# state (protect it) and in the cluster; prefer secrets_mode=existing with External Secrets/Vault where possible.
terraform {
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes" }
  }
}

variable "namespace" {
  type = string
}

variable "fullname" {
  description = "Chart fullname (service name prefix), e.g. roracle."
  type        = string
}

variable "names" {
  type = map(string)
}

variable "data" {
  type      = any
  sensitive = true
}

variable "postgres_mode" {
  type = string
}

variable "elastic_mode" {
  type = string
}

variable "minio_tenant" {
  type = bool
}

locals {
  cnpg   = var.postgres_mode == "cnpg"
  eck    = var.elastic_mode == "eck"
  tenant = var.minio_tenant
  d      = var.data

  db_url = local.d.database_url != "" ? local.d.database_url : "postgresql+psycopg://roracle:${urlencode(local.d.db_password)}@${var.fullname}-pg-rw:5432/roracle"
  es_url = local.d.elastic_url != "" ? local.d.elastic_url : "https://roracle_app:${urlencode(local.d.es_app_password)}@${var.fullname}-es-http.${var.namespace}.svc:9200"

  s3_access = local.tenant ? try(local.d.minio_app.access, "") : local.d.s3_access_key
  s3_secret = local.tenant ? try(local.d.minio_app.secret, "") : local.d.s3_secret_key
}

resource "kubernetes_secret_v1" "app" {
  metadata {
    name      = var.names.app
    namespace = var.namespace
  }
  data = merge(
    {
      ROR_DATABASE_URL  = local.db_url
      ROR_ELASTIC_URL   = local.es_url
      ROR_S3_ACCESS_KEY = local.s3_access
      ROR_S3_SECRET_KEY = local.s3_secret
    },
    local.d.llm_api_key == "" ? {} : { ROR_LLM_API_KEY = local.d.llm_api_key },
    local.d.extra,
  )
  lifecycle {
    precondition {
      condition     = nonsensitive(local.s3_access != "" && local.s3_secret != "")
      error_message = "S3 credentials missing: set secret_data.minio_app (tenant) or secret_data.s3_access_key/s3_secret_key (external)."
    }
  }
}

resource "kubernetes_secret_v1" "pg_app" {
  count = local.cnpg ? 1 : 0
  metadata {
    name      = var.names.pg_app
    namespace = var.namespace
  }
  type = "kubernetes.io/basic-auth"
  data = { username = "roracle", password = local.d.db_password }
  lifecycle {
    precondition {
      condition     = nonsensitive(local.d.db_password != "")
      error_message = "secret_data.db_password is required for postgres_mode=cnpg."
    }
  }
}

resource "kubernetes_secret_v1" "pg_backup" {
  count = local.cnpg ? 1 : 0
  metadata {
    name      = var.names.pg_backup
    namespace = var.namespace
  }
  data = {
    ACCESS_KEY_ID     = try(local.d.minio_pg_backup.access, "")
    ACCESS_SECRET_KEY = try(local.d.minio_pg_backup.secret, "")
  }
}

# CA of the MinIO operator auto-cert (= the cluster root CA); the chart fails to render without it (tenant + requestAutoCert).
data "kubernetes_config_map_v1" "root_ca" {
  count = local.cnpg && local.tenant ? 1 : 0
  metadata {
    name      = "kube-root-ca.crt"
    namespace = var.namespace
  }
}

resource "kubernetes_secret_v1" "pg_backup_ca" {
  count = local.cnpg && local.tenant ? 1 : 0
  metadata {
    name      = var.names.pg_backup_ca
    namespace = var.namespace
  }
  data = { "ca.crt" = data.kubernetes_config_map_v1.root_ca[0].data["ca.crt"] }
}

resource "kubernetes_secret_v1" "es_app" {
  count = local.eck ? 1 : 0
  metadata {
    name      = var.names.es_app
    namespace = var.namespace
  }
  type = "kubernetes.io/basic-auth"
  data = { username = "roracle_app", password = local.d.es_app_password, roles = "roracle_app" }
  lifecycle {
    precondition {
      condition     = nonsensitive(local.d.es_app_password != "")
      error_message = "secret_data.es_app_password is required for elasticsearch_mode=eck."
    }
  }
}

resource "kubernetes_secret_v1" "es_snapshot" {
  count = local.eck ? 1 : 0
  metadata {
    name      = var.names.es_snapshot
    namespace = var.namespace
  }
  data = {
    "s3.client.default.access_key" = try(local.d.minio_es_snapshot.access, "")
    "s3.client.default.secret_key" = try(local.d.minio_es_snapshot.secret, "")
  }
}

resource "kubernetes_secret_v1" "minio_config" {
  count = local.tenant ? 1 : 0
  metadata {
    name      = var.names.minio_config
    namespace = var.namespace
  }
  data = {
    "config.env" = "export MINIO_ROOT_USER=${local.d.minio_root_user}\nexport MINIO_ROOT_PASSWORD=${local.d.minio_root_pass}\n"
  }
  lifecycle {
    precondition {
      condition     = nonsensitive(local.d.minio_root_user != "" && local.d.minio_root_pass != "")
      error_message = "secret_data.minio_root_user/minio_root_pass are required for minio_mode=tenant."
    }
  }
}

locals {
  minio_users = {
    app         = { name = var.names.minio_app, cred = try(local.d.minio_app, null) }
    pg_backup   = { name = var.names.minio_pg_backup, cred = try(local.d.minio_pg_backup, null) }
    es_snapshot = { name = var.names.minio_es_snap, cred = try(local.d.minio_es_snapshot, null) }
  }
}

resource "kubernetes_secret_v1" "minio_user" {
  for_each = local.tenant ? toset(["app", "pg_backup", "es_snapshot"]) : toset([])
  metadata {
    name      = local.minio_users[each.key].name
    namespace = var.namespace
  }
  data = {
    CONSOLE_ACCESS_KEY = try(local.minio_users[each.key].cred.access, "")
    CONSOLE_SECRET_KEY = try(local.minio_users[each.key].cred.secret, "")
  }
}
