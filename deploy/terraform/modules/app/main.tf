# rOracle application release. Values arrive as ordered YAML documents (Helm deep-merges, last wins);
# the optional extra values file is appended last.
terraform {
  required_providers {
    helm = { source = "hashicorp/helm" }
  }
}

variable "release_name" {
  type = string
}

variable "namespace" {
  type = string
}

variable "chart" {
  type = object({
    path       = optional(string)
    repository = optional(string, "")
    version    = optional(string, "")
  })
}

variable "values_docs" {
  type = list(string)
}

variable "extra_values" {
  type    = string
  default = ""
}

variable "timeout_s" {
  type    = number
  default = 1800
}

locals {
  remote = var.chart.repository != ""
}

variable "protect" {
  description = "true: release has lifecycle.prevent_destroy (a destroy/replace plan errors; the data tier PVCs are deleted with the CRs on uninstall)."
  type        = bool
  default     = true
}

locals {
  chart_ref = local.remote ? "roracle" : coalesce(var.chart.path, "${path.root}/../helm/roracle")
  values    = concat(var.values_docs, var.extra_values == "" ? [] : [file(var.extra_values)])
}

# Upgrade path from the single-resource layout: keeps existing states from uninstalling the release (protect=true).
moved {
  from = helm_release.roracle
  to   = helm_release.protected[0]
}

# lifecycle.prevent_destroy cannot take a variable, so there are two identical resources selected by var.protect.
# Switching var.protect on a live install: `terraform state mv` between them first (README), never a plain apply.
resource "helm_release" "protected" {
  count       = var.protect ? 1 : 0
  name        = var.release_name
  namespace   = var.namespace
  repository  = local.remote ? var.chart.repository : null
  chart       = local.chart_ref
  version     = local.remote ? var.chart.version : null
  values      = local.values
  timeout     = var.timeout_s # chart hooks (migrate, minio-policy, ES snapshot repo) wait on operator-managed services
  wait        = false         # hooks still block; wait=true deadlocks: post-install migrate (cnpg) only runs after helm saw api/worker Ready
  atomic      = false
  max_history = 10

  lifecycle {
    prevent_destroy = true
  }
}

resource "helm_release" "unprotected" {
  count       = var.protect ? 0 : 1
  name        = var.release_name
  namespace   = var.namespace
  repository  = local.remote ? var.chart.repository : null
  chart       = local.chart_ref
  version     = local.remote ? var.chart.version : null
  values      = local.values
  timeout     = var.timeout_s
  wait        = false # hooks still block; wait=true deadlocks: post-install migrate (cnpg) only runs after helm saw api/worker Ready
  atomic      = false
  max_history = 10
}

output "status" {
  value = {
    name    = var.release_name
    version = one(concat(helm_release.protected[*].metadata.version, helm_release.unprotected[*].metadata.version))
    status  = one(concat(helm_release.protected[*].status, helm_release.unprotected[*].status))
  }
}
