# Operator layer: one helm_release per enabled chart (charts come from the air-gap mirror) + CRD readiness gate.
terraform {
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes" }
    helm       = { source = "hashicorp/helm" }
  }
}

variable "releases" {
  description = "Enabled charts keyed cnpg|barman|cert_manager|eck|minio_operator|kube_prometheus_stack|loki|otel_collector."
  type = map(object({
    repository = string
    chart      = string
    version    = string
    namespace  = string
    values     = list(string)
  }))
}

variable "timeout_s" {
  type    = number
  default = 1800
}

variable "loki_s3" {
  type      = object({ access_key = string, secret_key = string })
  default   = null
  sensitive = true
}

variable "pull_secret" {
  type = object({
    name     = string
    server   = string
    username = string
    password = string
  })
  default   = null
  sensitive = true
}

variable "pull_secret_enabled" {
  type    = bool
  default = false
}

variable "loki_s3_enabled" {
  description = "Non-sensitive flag for loki_s3 being set."
  type        = bool
  default     = false
}

locals {
  # Helm release names; kps/otel match the service names hard-wired in deploy/observability/*.values.yaml.
  release_name = {
    cnpg                  = "cnpg"
    barman                = "plugin-barman-cloud"
    cert_manager          = "cert-manager"
    eck                   = "eck-operator"
    minio_operator        = "minio-operator"
    kube_prometheus_stack = "kps"
    loki                  = "loki"
    otel_collector        = "otel"
  }
  # CRDs that must exist before the application chart renders its CRs.
  crds = {
    cnpg           = ["clusters.postgresql.cnpg.io"]
    barman         = ["objectstores.barmancloud.cnpg.io"]
    cert_manager   = ["certificates.cert-manager.io"]
    eck            = ["elasticsearches.elasticsearch.k8s.elastic.co"]
    minio_operator = ["tenants.minio.min.io"]
  }
  wanted_crds = toset(flatten([for k, v in local.crds : v if contains(keys(var.releases), k)]))
  namespaces  = toset([for r in var.releases : r.namespace])
}

resource "kubernetes_namespace_v1" "this" {
  for_each = local.namespaces
  metadata {
    name   = each.key
    labels = { "app.kubernetes.io/managed-by" = "terraform" }
  }
}

# Mirror credentials in every operator namespace (operators and their operands pull from the mirror).
resource "kubernetes_secret_v1" "pull" {
  for_each = var.pull_secret_enabled ? local.namespaces : toset([])
  metadata {
    name      = var.pull_secret.name
    namespace = kubernetes_namespace_v1.this[each.key].metadata[0].name
  }
  type = "kubernetes.io/dockerconfigjson"
  data = {
    ".dockerconfigjson" = jsonencode({
      auths = { (var.pull_secret.server) = {
        username = var.pull_secret.username
        password = var.pull_secret.password
        auth     = base64encode("${var.pull_secret.username}:${var.pull_secret.password}")
      } }
    })
  }
}

resource "kubernetes_secret_v1" "loki_s3" {
  count = var.loki_s3_enabled && contains(keys(var.releases), "loki") ? 1 : 0
  metadata {
    name      = "loki-s3"
    namespace = kubernetes_namespace_v1.this[var.releases["loki"].namespace].metadata[0].name
  }
  data = {
    AWS_ACCESS_KEY_ID     = var.loki_s3.access_key
    AWS_SECRET_ACCESS_KEY = var.loki_s3.secret_key
  }
}

resource "helm_release" "base" {
  for_each = { for k, v in var.releases : k => v if k != "barman" }

  name        = local.release_name[each.key]
  repository  = each.value.repository
  chart       = each.value.chart
  version     = each.value.version
  namespace   = kubernetes_namespace_v1.this[each.value.namespace].metadata[0].name
  values      = each.value.values
  timeout     = var.timeout_s
  wait        = true
  max_history = 10

  depends_on = [kubernetes_secret_v1.loki_s3, kubernetes_secret_v1.pull] # loki reads Secret loki-s3
}

# The barman-cloud plugin needs the CNPG operator and cert-manager (webhook certificates) first.
resource "helm_release" "barman" {
  for_each = { for k, v in var.releases : k => v if k == "barman" }

  name        = local.release_name[each.key]
  repository  = each.value.repository
  chart       = each.value.chart
  version     = each.value.version
  namespace   = kubernetes_namespace_v1.this[each.value.namespace].metadata[0].name
  values      = each.value.values
  timeout     = var.timeout_s
  wait        = true
  max_history = 10

  depends_on = [helm_release.base, kubernetes_secret_v1.pull]
}

data "kubernetes_resource" "crd" {
  for_each    = local.wanted_crds
  api_version = "apiextensions.k8s.io/v1"
  kind        = "CustomResourceDefinition"
  metadata {
    name = each.key
  }
  depends_on = [helm_release.base, helm_release.barman]
}

resource "terraform_data" "ready" {
  input      = sort(keys(data.kubernetes_resource.crd))
  depends_on = [data.kubernetes_resource.crd]
}

output "ready" {
  description = "Resolves once every operator CRD the app needs exists."
  value       = terraform_data.ready.id
}
