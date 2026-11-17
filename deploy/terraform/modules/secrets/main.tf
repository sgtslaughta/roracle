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

variable "llm_key_used" {
  description = "Chart secrets.llmApiKeyEnabled: the key is then mandatory (non-optional secretKeyRef)."
  type        = bool
  default     = true
}

variable "s3_in_app" {
  description = "false when the S3 keys live in their own Secrets (rook-ceph; created by modules/ceph): no S3 keys, pg_backup or es_snapshot Secrets here."
  type        = bool
  default     = true
}

variable "pg_backup_required" {
  type    = bool
  default = true
}

variable "es_snapshot_required" {
  type    = bool
  default = true
}

variable "s3_external" {
  description = "true = external S3: backup identities must be separate from the app identity."
  type        = bool
  default     = true
}

variable "s3" {
  description = "{app, pg_backup, es_snapshot} => {access, secret}"
  type        = map(object({ access = string, secret = string }))
  sensitive   = true
}

locals {
  cnpg = var.postgres_mode == "cnpg"
  eck  = var.elastic_mode == "eck"
  d    = var.data

  db_url = local.d.database_url != "" ? local.d.database_url : "postgresql+psycopg://roracle:${urlencode(local.d.db_password)}@${var.fullname}-pg-rw:5432/roracle"
  es_url = local.d.elastic_url != "" ? local.d.elastic_url : "https://roracle_app:${urlencode(local.d.es_app_password)}@${var.fullname}-es-http.${var.namespace}.svc:9200"

  s3_access = try(var.s3["app"].access, "")
  s3_secret = try(var.s3["app"].secret, "")
}

resource "kubernetes_secret_v1" "app" {
  metadata {
    name      = var.names.app
    namespace = var.namespace
  }
  data = merge(
    {
      ROR_DATABASE_URL = local.db_url
      ROR_ELASTIC_URL  = local.es_url
    },
    var.s3_in_app ? { ROR_S3_ACCESS_KEY = local.s3_access, ROR_S3_SECRET_KEY = local.s3_secret } : {},
    local.d.llm_api_key == "" ? {} : { ROR_LLM_API_KEY = local.d.llm_api_key },
    local.d.extra,
  )
  lifecycle {
    precondition {
      condition     = !var.llm_key_used || nonsensitive(local.d.llm_api_key != "")
      error_message = "secret_data.llm_api_key is required while models.llm_key_used=true (set models.llm_key_used=false if the endpoint needs no key)."
    }
    precondition {
      condition     = nonsensitive(!(var.s3_external && local.cnpg && var.pg_backup_required) || (try(var.s3["pg_backup"].access, "") != "" && try(var.s3["pg_backup"].access, "") != local.s3_access))
      error_message = "postgres.backup is on: secret_data.s3_pg_backup must be set and use a different access key than s3_access_key (the app identity must not be able to write or delete backups). Or set backups_enabled.postgres=false."
    }
    precondition {
      condition     = nonsensitive(!(var.s3_external && local.eck && var.es_snapshot_required) || (try(var.s3["es_snapshot"].access, "") != "" && try(var.s3["es_snapshot"].access, "") != local.s3_access))
      error_message = "elasticsearch.snapshot is on: secret_data.s3_es_snapshot must be set and use a different access key than s3_access_key. Or set backups_enabled.elasticsearch=false."
    }
    precondition {
      condition     = nonsensitive(!var.s3_in_app || (local.s3_access != "" && local.s3_secret != ""))
      error_message = "S3 credentials missing: set secret_data.s3_access_key/s3_secret_key (external) or use object_store=rook-ceph."
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
  count = local.cnpg && var.s3_in_app ? 1 : 0
  metadata {
    name      = var.names.pg_backup
    namespace = var.namespace
  }
  data = {
    ACCESS_KEY_ID     = try(var.s3["pg_backup"].access, "")
    ACCESS_SECRET_KEY = try(var.s3["pg_backup"].secret, "")
  }
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
  count = local.eck && var.s3_in_app ? 1 : 0
  metadata {
    name      = var.names.es_snapshot
    namespace = var.namespace
  }
  data = {
    "s3.client.default.access_key" = try(var.s3["es_snapshot"].access, "")
    "s3.client.default.secret_key" = try(var.s3["es_snapshot"].secret, "")
  }
}
