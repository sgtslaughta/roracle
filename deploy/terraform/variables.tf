# ---- Cluster access (cluster is pre-existing; no cloud resources are managed) ----
variable "kubeconfig_path" {
  description = "Path to the kubeconfig used by the kubernetes and helm providers."
  type        = string
  default     = "~/.kube/config"
}

variable "kubeconfig_context" {
  description = "kubeconfig context to target. null = the file's current-context."
  type        = string
  default     = null
}

# ---- Placeholder gate ----
variable "allow_placeholders" {
  description = "false (default): plan works but apply FAILS while any enabled chart/image/registry still holds a PLACEHOLDER. true only for review/dry runs."
  type        = bool
  default     = false
}

variable "mirror_registry" {
  description = "Air-gap image registry host[/path] (no scheme). Substituted for MIRROR.example.com in the observability values files and used as the app global.imageRegistry."
  type        = string
  default     = "MIRROR.example.com"
}

# ---- Namespaces, quota, pull secret ----
variable "namespaces" {
  description = "Namespace names. app = rOracle release namespace (created here). Operator namespaces are created by Terraform (modules/operators) and MUST match the chart's networkPolicy.operatorNamespaces."
  type = object({
    app           = optional(string, "roracle")
    cnpg          = optional(string, "cnpg-system")
    cert_manager  = optional(string, "cert-manager")
    eck           = optional(string, "elastic-system")
    rook_ceph     = optional(string, "rook-ceph")
    monitoring    = optional(string, "monitoring")
    observability = optional(string, "observability")
    inference     = optional(string, "roracle-inference")
  })
  default = {}
}

variable "quota" {
  description = "Optional ResourceQuota + LimitRange in the app namespace. null = none. Size above the full bundled data tier (see deployment guide section 3)."
  type = object({
    requests_cpu    = string
    requests_memory = string
    limits_memory   = string
    pvcs            = optional(number, 30)
    storage         = optional(string, "2Ti")
    default_request = optional(object({ cpu = string, memory = string }), { cpu = "100m", memory = "128Mi" })
    default_limit   = optional(object({ memory = string }), { memory = "1Gi" })
  })
  default = null
}

variable "pull_secret" {
  description = "Mirror registry credentials -> kubernetes.io/dockerconfigjson Secret in the app namespace (and the inference namespace when enabled). null = none."
  type = object({
    name     = optional(string, "regcred")
    server   = string
    username = string
    password = string
  })
  default   = null
  sensitive = true
}

# ---- Operator layer ----
variable "charts" {
  description = <<-EOT
    Per-chart overrides, keys: cnpg, barman, cert_manager, eck, rook_ceph, rook_ceph_cluster, kube_prometheus_stack, loki, otel_collector.
    Any attribute left null falls back to the default in locals.tf (repository oci://MIRROR.example.com/charts, version 0.0.0-PLACEHOLDER).
    `values` = extra YAML string merged after the built-in values (e.g. file("my-cnpg.yaml")).
  EOT
  type = map(object({
    enabled    = optional(bool)
    repository = optional(string)
    chart      = optional(string)
    version    = optional(string)
    values     = optional(string)
  }))
  default = {}
}

variable "helm_timeout_s" {
  description = "Per-release helm timeout in seconds (the rOracle chart has post-install hooks that wait on operator-managed services)."
  type        = number
  default     = 1800
}

variable "loki_s3_credentials" {
  description = "Creates Secret loki-s3 (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY) in the observability namespace when loki is enabled. null = you create it."
  type        = object({ access_key = string, secret_key = string })
  default     = null
  sensitive   = true
}

variable "operator_images" {
  description = <<-EOT
    Operator image overrides for the air-gap mirror, keys: cnpg, barman, cert_manager (repository = prefix, -controller/-webhook/-cainjector/-startupapicheck appended), eck, rook_ceph.
    Defaults: repository "<mirror_registry>/<upstream path>", tag "0.0.0-PLACEHOLDER" (gated). barman: images render <mirror_registry>/cloudnative-pg/plugin-barman-cloud[-sidecar]:<tag> via image.registry/sidecarImage.registry (chart 0.9.0 keys; the repository override is ignored for barman). Chart value keys are the common ones; verify against the mirrored chart and use charts.<key>.values to adjust (e.g. the barman sidecar image).
  EOT
  type = map(object({
    repository  = optional(string)
    tag         = optional(string)
    sidecar_tag = optional(string) # barman only: tag of the plugin-barman-cloud-sidecar image
  }))
  default = {}
}
