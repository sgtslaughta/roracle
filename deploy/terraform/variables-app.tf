# ---- Application layer (helm chart deploy/helm/roracle) ----
variable "deploy_app" {
  description = "false = operator layer only (install operators first, then flip to true; see README plan/apply order)."
  type        = bool
  default     = true
}

variable "protect_data" {
  description = "true (default): prevent_destroy on the rOracle release, because uninstalling it deletes the data-tier CRs and their PVCs. Set false only for disposable environments (decide before first install; see README to switch)."
  type        = bool
  default     = true
}

variable "release_name" {
  type    = string
  default = "roracle"
}

variable "app_chart" {
  description = "Chart source. repository=\"\" uses the local chart at `path` (default deploy/helm/roracle). Set repository (oci://...) + version to pull a published chart from the mirror instead."
  type = object({
    path       = optional(string)
    repository = optional(string, "")
    version    = optional(string, "0.0.0-PLACEHOLDER")
  })
  default = {}
}

variable "extra_values_file" {
  description = "Optional YAML file merged AFTER the Terraform-generated values (wins on conflict). Use for anything not exposed as a variable."
  type        = string
  default     = ""
}

variable "images" {
  description = "Image source. registry = chart global.imageRegistry (empty = mirror_registry). Empty tag = chart appVersion; digest wins over tag."
  type = object({
    registry       = optional(string, "")
    backend_tag    = optional(string, "")
    backend_digest = optional(string, "")
    web_tag        = optional(string, "")
    web_digest     = optional(string, "")
    pull_policy    = optional(string, "IfNotPresent")
  })
  default = {}
}

variable "ingress" {
  type = object({
    enabled    = optional(bool, true)
    class_name = optional(string, "nginx")
    host       = optional(string, "")
    tls_secret = optional(string, "")
  })
  default = {}
}

variable "keycloak" {
  description = "OIDC (Keycloak) settings. issuer e.g. https://kc.example/realms/research; jwks_url defaults to <issuer>/protocol/openid-connect/certs. web_client_id = public SPA client (docs/enterprise/keycloak-setup.md). The chart has no confidential-client secret."
  type = object({
    issuer        = string
    jwks_url      = optional(string, "")
    audience      = optional(string, "roracle")
    web_client_id = optional(string, "roracle-web")
  })
}

variable "models" {
  description = "OpenAI-spec endpoints (ROR_*). llm_base_url serves chat AND embeddings. Empty urls fall back to the in-cluster inference module when enabled."
  type = object({
    llm_base_url = optional(string, "")
    chat_models  = optional(string, "")
    embed_model  = optional(string, "")
    embed_dims   = optional(number)
    rerank_url   = optional(string, "")
    rerank_model = optional(string, "")
    llm_key_used = optional(bool, true)
    tokenizer    = optional(string, "")
    extra_env    = optional(map(string), {})
  })
  default = {}
}

variable "ha" {
  description = "true = chart HA defaults (3 PG, 3 ES, Ceph or external S3, HPA, PDB). false = single-instance dev profile with small resources."
  type        = bool
  default     = false
}

variable "replicas" {
  description = "Override replica counts (null = derived from ha)."
  type = object({
    api    = optional(number)
    worker = optional(number)
    web    = optional(number)
  })
  default = {}
}

variable "storage" {
  type = object({
    postgres      = optional(string, "100Gi")
    elasticsearch = optional(string, "200Gi")
    # object storage capacity is the Ceph OSD devices (rook-ceph) or the external S3
    class = optional(string, "")
  })
  default = {}
}

variable "backups_enabled" {
  description = "chart postgres.backup / elasticsearch.snapshot. true needs S3 credentials separate from the app identity (external: secret_data.s3_pg_backup / s3_es_snapshot, different access key; rook-ceph: automatic)."
  type = object({
    postgres      = optional(bool, true)
    elasticsearch = optional(bool, true)
  })
  default = {}
}

variable "postgres_mode" {
  type    = string
  default = "cnpg"
  validation {
    condition     = contains(["cnpg", "external"], var.postgres_mode)
    error_message = "cnpg | external"
  }
}

variable "elasticsearch_mode" {
  type    = string
  default = "eck"
  validation {
    condition     = contains(["eck", "external"], var.elasticsearch_mode)
    error_message = "eck | external"
  }
}

variable "network" {
  description = "Chart networkPolicy inputs. Empty cidr lists leave traffic denied (kube_api empty = any destination on 443/6443: tighten)."
  type = object({
    ingress_namespace      = optional(string, "ingress-nginx")
    kube_api_cidrs         = optional(list(string), [])
    llm_cidrs              = optional(list(string), [])
    llm_ports              = optional(list(number), [443])
    oidc_cidrs             = optional(list(string), [])
    s3_cidrs               = optional(list(string), []) # external S3 egress (api/worker/migrate and the CNPG/ES data tier)
    s3_ports               = optional(list(number), [443, 9000])
    postgres_cidrs         = optional(list(string), [])
    elastic_cidrs          = optional(list(string), [])
    llm_namespace_selector = optional(map(string), {})
    llm_pod_selector       = optional(map(string), {})
  })
  default = {}
}

# ---- Secrets: create from sensitive vars, or reference existing ----
variable "secrets_mode" {
  description = "create: Terraform creates every Secret from secret_data (values live in state!). existing: nothing created; Secrets named in secret_names must exist (External Secrets / Vault / kubectl)."
  type        = string
  default     = "create"
  validation {
    condition     = contains(["create", "existing"], var.secrets_mode)
    error_message = "create | existing"
  }
}

variable "secret_names" {
  description = "Secret names consumed by the chart (created in create mode, expected in existing mode)."
  type = object({
    app         = optional(string, "roracle-secrets")
    pg_app      = optional(string, "roracle-pg-app")
    pg_backup   = optional(string, "roracle-pg-backup-s3")
    es_app      = optional(string, "roracle-es-app")
    s3          = optional(string, "roracle-s3") # rook-ceph: S3 keys of the app (own Secret, created by Terraform in every secrets_mode)
    es_snapshot = optional(string, "roracle-es-s3")
  })
  default = {}
}

variable "secret_data" {
  description = "Credentials for create mode. Charset of passwords: [A-Za-z0-9._~-] (openssl rand -hex 32). database_url/elastic_url override the generated URLs (external modes). extra goes into the app Secret (e.g. other model API keys)."
  type = object({
    db_password     = optional(string, "")
    es_app_password = optional(string, "")
    llm_api_key     = optional(string, "")
    rerank_api_key  = optional(string, "")
    database_url    = optional(string, "")
    elastic_url     = optional(string, "")
    s3_access_key   = optional(string, "")
    s3_secret_key   = optional(string, "")
    s3_pg_backup    = optional(object({ access = string, secret = string })) # external: defaults to s3_access_key/s3_secret_key
    s3_es_snapshot  = optional(object({ access = string, secret = string }))
    extra           = optional(map(string), {})
  })
  default   = {}
  sensitive = true
}

# ---- Optional in-cluster GPU inference ----
variable "inference" {
  description = "Optional in-cluster OpenAI-spec LLM/embedding server (vLLM-style) + reranker (bge-reranker-v2-m3; vLLM score engine, TEI optional). Disabled by default; images are placeholders."
  type = object({
    enabled = optional(bool, false)
    llm = optional(object({
      image         = optional(string, "MIRROR.example.com/vllm/vllm-openai:0.0.0-PLACEHOLDER")
      model_path    = optional(string, "/models/CHANGEME")
      served_name   = optional(string, "chat")
      args          = optional(list(string), [])
      gpus          = optional(number, 1)
      node_selector = optional(map(string), {})
      tolerations   = optional(list(object({ key = string, operator = optional(string, "Exists"), value = optional(string), effect = optional(string, "NoSchedule") })), [{ key = "nvidia.com/gpu" }])
      models_pvc    = optional(string, "inference-models")
      cpu           = optional(string, "4")
      memory        = optional(string, "32Gi")
      run_as_user   = optional(number, 10001)
    }), {})
    rerank = optional(object({
      engine          = optional(string, "vllm") # vllm (Cohere-style /v1/rerank, the backend default) | tei (/rerank; also set chart rerank.api=tei via extra_values_file)
      api_key_enabled = optional(bool, false)    # server-side key; the backend sends ROR_RERANK_API_KEY as a Bearer token (chart secrets.rerankApiKeyEnabled, not set by this stack)
      image           = optional(string, "MIRROR.example.com/vllm/vllm-openai:0.0.0-PLACEHOLDER")
      model_path      = optional(string, "/models/bge-reranker-v2-m3")
      served_name     = optional(string, "bge-reranker-v2-m3")
      args            = optional(list(string)) # null = engine default (vllm: ["--task","score"]; change/clear per mirrored vLLM version)
      gpus            = optional(number, 1)
      node_selector   = optional(map(string), {})
      tolerations     = optional(list(object({ key = string, operator = optional(string, "Exists"), value = optional(string), effect = optional(string, "NoSchedule") })), [{ key = "nvidia.com/gpu" }])
      models_pvc      = optional(string, "inference-models")
      cpu             = optional(string, "2")
      memory          = optional(string, "8Gi")
      run_as_user     = optional(number, 10001)
    }), {})
  })
  default = {}
}
