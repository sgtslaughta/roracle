# Namespaces, optional quota/limits, image pull secret. Pure Kubernetes objects; no cloud resources.
terraform {
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes" }
  }
}

variable "app_namespace" {
  type = string
}

variable "extra_namespaces" {
  description = "Additional namespaces to create (e.g. inference)."
  type        = list(string)
  default     = []
}

variable "quota" {
  type = object({
    requests_cpu    = string
    requests_memory = string
    limits_memory   = string
    pvcs            = number
    storage         = string
    default_request = object({ cpu = string, memory = string })
    default_limit   = object({ memory = string })
  })
  default = null
}

variable "pull_secret_enabled" {
  description = "Non-sensitive flag (for_each cannot key on a sensitive value)."
  type        = bool
  default     = false
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

variable "pod_security" {
  description = "Pod Security Admission levels on the app namespace. \"\" = label not set. Default enforces nothing; warn/audit surface violations."
  type = object({
    enforce = optional(string, "")
    warn    = optional(string, "restricted")
    audit   = optional(string, "restricted")
  })
  default = {}
  validation {
    condition     = alltrue([for v in values(var.pod_security) : contains(["", "privileged", "baseline", "restricted"], v)])
    error_message = "pod_security levels must each be one of: \"\", privileged, baseline, restricted."
  }
}

locals {
  namespaces = toset(concat([var.app_namespace], var.extra_namespaces))
}

resource "kubernetes_namespace_v1" "this" {
  for_each = local.namespaces
  metadata {
    name = each.key
    labels = merge(
      { "app.kubernetes.io/part-of" = "roracle", "app.kubernetes.io/managed-by" = "terraform" },
      each.key == var.app_namespace ? { for m, l in var.pod_security : "pod-security.kubernetes.io/${m}" => l if l != "" } : {},
    )
  }
}

resource "kubernetes_resource_quota_v1" "app" {
  count = var.quota == null ? 0 : 1
  metadata {
    name      = "roracle"
    namespace = kubernetes_namespace_v1.this[var.app_namespace].metadata[0].name
  }
  spec {
    hard = {
      "requests.cpu"           = var.quota.requests_cpu
      "requests.memory"        = var.quota.requests_memory
      "limits.memory"          = var.quota.limits_memory
      "persistentvolumeclaims" = tostring(var.quota.pvcs)
      "requests.storage"       = var.quota.storage
    }
  }
}

resource "kubernetes_limit_range_v1" "app" {
  count = var.quota == null ? 0 : 1
  metadata {
    name      = "roracle"
    namespace = kubernetes_namespace_v1.this[var.app_namespace].metadata[0].name
  }
  spec {
    limit {
      type            = "Container"
      default_request = var.quota.default_request
      default         = var.quota.default_limit
    }
  }
}

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

output "app_namespace" {
  value = kubernetes_namespace_v1.this[var.app_namespace].metadata[0].name
}
