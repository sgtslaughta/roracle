# Order: placeholder gate -> platform (namespaces/secrets) -> operators -> CRDs ready -> app.

check "placeholders" {
  assert {
    condition     = var.allow_placeholders || length(local.placeholders) == 0
    error_message = "Placeholders remain: ${join(", ", local.placeholders)}. apply will fail until replaced (or allow_placeholders=true)."
  }
}

resource "terraform_data" "placeholder_gate" {
  # Replaced on every run: self.id is unknown at plan (postcondition deferred to apply) and a failed apply cannot leave a
  # sticky gate in state that would make later plans fail.
  triggers_replace = plantimestamp()

  # postcondition (not precondition): evaluated while applying, so `plan` still works for review.
  lifecycle {
    postcondition {
      # self.id is unknown during plan, which defers the whole condition to apply time.
      condition     = var.allow_placeholders || length(local.placeholders) == 0 || self.id == ""
      error_message = "Placeholders remain (set real mirror values, or allow_placeholders=true for plan-only review): ${join(", ", local.placeholders)}"
    }
  }
}

module "platform" {
  source              = "./modules/platform"
  app_namespace       = var.namespaces.app
  quota               = var.quota
  pull_secret         = var.pull_secret
  pull_secret_enabled = nonsensitive(var.pull_secret != null)
  extra_namespaces = (
    local.inference_enabled ? [var.namespaces.inference] : []
  )
  depends_on = [terraform_data.placeholder_gate]
}

module "operators" {
  source              = "./modules/operators"
  releases            = local.operator_charts
  timeout_s           = var.helm_timeout_s
  loki_s3             = var.loki_s3_credentials
  loki_s3_enabled     = nonsensitive(var.loki_s3_credentials != null)
  pull_secret         = var.pull_secret
  pull_secret_enabled = nonsensitive(var.pull_secret != null)
  depends_on          = [module.platform]
}

module "secrets" {
  count         = var.deploy_app && var.secrets_mode == "create" ? 1 : 0
  source        = "./modules/secrets"
  namespace     = var.namespaces.app
  names         = var.secret_names
  data          = var.secret_data
  fullname      = local.app_fullname
  postgres_mode = var.postgres_mode
  minio_tenant  = var.minio_mode == "tenant"
  llm_key_used  = var.models.llm_key_used
  elastic_mode  = var.elasticsearch_mode
  depends_on    = [module.platform]
}

module "inference" {
  count              = local.inference_enabled ? 1 : 0
  source             = "./modules/inference"
  namespace          = var.namespaces.inference
  llm                = var.inference.llm
  rerank             = var.inference.rerank
  pull_secret_names  = nonsensitive(var.pull_secret == null ? [] : [var.pull_secret.name])
  app_namespace      = var.namespaces.app
  release_name       = var.release_name
  create_keys_secret = var.secrets_mode == "create"
  llm_api_key        = var.secret_data.llm_api_key
  rerank_api_key     = var.secret_data.rerank_api_key
  depends_on         = [module.platform, terraform_data.placeholder_gate]
}

module "app" {
  count  = var.deploy_app && !(local.app_chart_remote && length(regexall(local.marker, "${var.app_chart.repository} ${var.app_chart.version}")) > 0) ? 1 : 0
  source = "./modules/app"

  release_name = var.release_name
  protect      = var.protect_data
  namespace    = var.namespaces.app
  chart        = var.app_chart
  timeout_s    = var.helm_timeout_s
  values_docs  = local.app_values_docs
  extra_values = var.extra_values_file

  # operators -> CRDs ready -> secrets -> app
  depends_on = [module.operators, module.secrets, module.inference]
}
