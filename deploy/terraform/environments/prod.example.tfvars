# Production / HA profile. Copy to environments/prod.tfvars (git-ignored). Secrets via TF_VAR_* or secrets_mode=existing.
kubeconfig_path    = "~/.kube/config"
kubeconfig_context = "prod-cluster"

mirror_registry = "registry.internal.example"
ha              = true # 3 PG, 3 ES, 4x4 MinIO, HPA + PDB, topology spread (chart defaults)

charts = {
  cnpg                  = { repository = "oci://registry.internal.example/charts", version = "0.26.0" }
  barman                = { repository = "oci://registry.internal.example/charts", version = "0.9.0" }
  cert_manager          = { repository = "oci://registry.internal.example/charts", version = "v1.17.0" }
  eck                   = { repository = "oci://registry.internal.example/charts", version = "2.16.0" }
  minio_operator        = { repository = "oci://registry.internal.example/charts", version = "7.0.0" }
  kube_prometheus_stack = { enabled = true, repository = "oci://registry.internal.example/charts", version = "70.0.0" }
  loki                  = { enabled = true, repository = "oci://registry.internal.example/charts", version = "6.30.0" }
  otel_collector        = { enabled = true, repository = "oci://registry.internal.example/charts", version = "0.120.0" }
}

# Observability wiring (values are not set by Terraform): in extra_values_file set observability.otel.endpoint,
# observability.grafanaDashboard.enabled=true and networkPolicy.egressCIDRs.otel (see deploy/observability/README.md).
# loki also needs `loki_s3_credentials` (or your own Secret loki-s3) and charts.loki.values overriding the s3 endpoint.

# Secrets from an external manager (External Secrets, Vault CSI, sealed): Terraform creates none and keeps no values in state.
secrets_mode = "existing" # all names in secret_names (defaults) must exist in the app namespace before apply

# Operator images from the mirror (repository defaults to <mirror_registry>/<upstream path>; tag is a gated placeholder).
operator_images = {
  cnpg           = { tag = "1.26.0" }
  barman         = { tag = "v0.16.0", sidecar_tag = "v0.16.0" } # chart 0.9.0 ships v0.16.0
  cert_manager   = { tag = "v1.17.0" }
  eck            = { tag = "2.16.0" }
  minio_operator = { tag = "v7.0.0" }
}

protect_data = true # default: prevent_destroy on the release (uninstall would delete data-tier PVCs)

quota = {
  requests_cpu    = "120"
  requests_memory = "400Gi"
  limits_memory   = "480Gi"
  pvcs            = 60
  storage         = "6Ti"
}

images = {
  registry       = "registry.internal.example"
  backend_tag    = "0.1.0"
  backend_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  web_tag        = "0.1.0"
  web_digest     = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}

ingress = {
  host       = "roracle.example.internal"
  tls_secret = "roracle-tls"
}

keycloak = {
  issuer = "https://kc.example.internal/realms/research"
}

models = {
  llm_base_url = "https://llm.example.internal/v1"
  chat_models  = "qwen2.5:14b"
  embed_model  = "embeddinggemma"
  embed_dims   = 768
  rerank_url   = "https://rerank.example.internal/v1/rerank"
  rerank_model = "bge-reranker-v2-m3"
}

network = {
  kube_api_cidrs = ["10.0.0.1/32"] # apiserver endpoint IPs (`kubectl get endpoints kubernetes`), never the Service ClusterIP
  llm_cidrs      = ["10.70.0.0/24"]
  oidc_cidrs     = ["10.71.0.0/24"]
}

storage = { postgres = "500Gi", elasticsearch = "1Ti", minio = "1Ti", class = "fast-ssd" }

# Optional in-cluster GPU inference instead of the external endpoints above (set models.*_url = "" to use it):
# inference = { enabled = true, llm = { image = "registry.internal.example/vllm/vllm-openai:v0.x", model_path = "/models/<dir>" },
#               rerank = { image = "registry.internal.example/huggingface/text-embeddings-inference:x.y" },
#               ... node_selector / tolerations per GPU pool }
