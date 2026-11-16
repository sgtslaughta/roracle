locals {
  placeholder_repo    = "oci://MIRROR.example.com/charts"
  placeholder_version = "0.0.0-PLACEHOLDER"
  obs_dir             = "${path.module}/../observability"

  # Defaults per chart. Override any attribute through var.charts[<key>].
  chart_defaults = {
    cnpg                  = { enabled = true, chart = "cloudnative-pg", ns = var.namespaces.cnpg }
    barman                = { enabled = true, chart = "plugin-barman-cloud", ns = var.namespaces.cnpg }
    cert_manager          = { enabled = true, chart = "cert-manager", ns = var.namespaces.cert_manager }
    eck                   = { enabled = true, chart = "eck-operator", ns = var.namespaces.eck }
    minio_operator        = { enabled = true, chart = "operator", ns = var.namespaces.minio }
    kube_prometheus_stack = { enabled = false, chart = "kube-prometheus-stack", ns = var.namespaces.monitoring }
    loki                  = { enabled = false, chart = "loki", ns = var.namespaces.observability }
    otel_collector        = { enabled = false, chart = "opentelemetry-collector", ns = var.namespaces.observability }
  }

  # Built-in values (YAML). Observability files are reused verbatim with the mirror host substituted.
  builtin_values = {
    cnpg                  = ""
    barman                = ""
    cert_manager          = yamlencode({ crds = { enabled = true } })
    eck                   = ""
    minio_operator        = ""
    kube_prometheus_stack = replace(file("${local.obs_dir}/kube-prometheus-stack.values.yaml"), "MIRROR.example.com", var.mirror_registry)
    loki                  = replace(file("${local.obs_dir}/loki.values.yaml"), "MIRROR.example.com", var.mirror_registry)
    otel_collector        = replace(file("${local.obs_dir}/otel-collector.values.yaml"), "MIRROR.example.com", var.mirror_registry)
  }

  charts = {
    for k, d in local.chart_defaults : k => {
      enabled    = coalesce(try(var.charts[k].enabled, null), d.enabled)
      repository = coalesce(try(var.charts[k].repository, null), local.placeholder_repo)
      chart      = coalesce(try(var.charts[k].chart, null), d.chart)
      version    = coalesce(try(var.charts[k].version, null), local.placeholder_version)
      namespace  = d.ns
      values     = compact([local.builtin_values[k], try(var.charts[k].values, null)])
    }
  }

  operator_charts   = { for k, c in local.charts : k => c if c.enabled }
  inference_enabled = var.inference.enabled
  app_chart_remote  = var.app_chart.repository != ""

  # Anything below containing a placeholder marker blocks apply (see terraform_data.placeholder_gate).
  placeholder_probe = merge(
    { for k, c in local.operator_charts : "chart.${k}.repository" => c.repository },
    { for k, c in local.operator_charts : "chart.${k}.version" => c.version },
    { "var.mirror_registry" = var.mirror_registry },
    local.app_chart_remote ? { "app_chart.repository" = var.app_chart.repository, "app_chart.version" = var.app_chart.version } : {},
    local.inference_enabled ? { "inference.llm_image" = var.inference.llm.image, "inference.rerank_image" = var.inference.rerank.image } : {},
  )
  placeholders = sort([
    for k, v in local.placeholder_probe : k
    if length(regexall("PLACEHOLDER|MIRROR\\.example\\.com", v)) > 0
  ])
}
