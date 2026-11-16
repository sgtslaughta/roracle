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

resource "helm_release" "roracle" {
  name       = var.release_name
  namespace  = var.namespace
  repository = local.remote ? var.chart.repository : null
  chart      = local.remote ? "roracle" : coalesce(var.chart.path, "${path.root}/../helm/roracle")
  version    = local.remote ? var.chart.version : null
  values     = concat(var.values_docs, var.extra_values == "" ? [] : [file(var.extra_values)])

  # Chart hooks (migrate, minio-policy, ES snapshot repo) wait on operator-managed services.
  timeout = var.timeout_s
  wait    = true
  atomic  = false
}

output "status" {
  value = {
    name    = helm_release.roracle.name
    version = helm_release.roracle.metadata.version
    status  = helm_release.roracle.status
  }
}
