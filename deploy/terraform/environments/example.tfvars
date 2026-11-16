# Dev / single-node profile. Copy to environments/dev.tfvars (git-ignored) and fill in.
# Secrets: pass via TF_VAR_secret_data / TF_VAR_pull_secret (env) or a git-ignored *.auto.tfvars, never commit.
#   terraform plan  -var-file=environments/dev.tfvars -var allow_placeholders=true   # review only
kubeconfig_path    = "~/.kube/config"
kubeconfig_context = "dev-cluster"

mirror_registry = "registry.internal.example" # replaces MIRROR.example.com everywhere
ha              = false                       # 1 PG, 1 ES node, 1 MinIO server (4 drives), no HPA/PDB, small resources

# Operators: pin chart repo + version from your mirror (see README placeholder table).
charts = {
  cnpg           = { repository = "oci://registry.internal.example/charts", version = "0.26.0" }
  barman         = { repository = "oci://registry.internal.example/charts", version = "0.9.0" }
  cert_manager   = { repository = "oci://registry.internal.example/charts", version = "v1.17.0" }
  eck            = { repository = "oci://registry.internal.example/charts", version = "2.16.0" }
  minio_operator = { repository = "oci://registry.internal.example/charts", version = "7.0.0" }
}

# Operator images from the mirror (repository defaults to <mirror_registry>/<upstream path>; tag is a gated placeholder).
operator_images = {
  cnpg           = { tag = "1.26.0" }
  barman         = { tag = "v0.16.0", sidecar_tag = "v0.16.0" } # chart 0.9.0 ships v0.16.0
  cert_manager   = { tag = "v1.17.0" }
  eck            = { tag = "2.16.0" }
  minio_operator = { tag = "v7.0.0" }
}

protect_data = false # WARNING: never flip on a live install with a plain apply (re-creates the release, deletes data); disposable dev: allows `terraform destroy` (see README Destroy cautions)

quota = {
  requests_cpu    = "24"
  requests_memory = "64Gi"
  limits_memory   = "96Gi"
}

images = {
  registry       = "registry.internal.example"
  backend_digest = "sha256:0000000000000000000000000000000000000000000000000000000000000000" # pin the CI-pushed digest
  web_digest     = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}

ingress = {
  host       = "roracle.dev.example.internal"
  tls_secret = "roracle-tls" # pre-created (cert-manager Certificate or manual)
}

keycloak = {
  issuer = "https://kc.example.internal/realms/research"
}

models = {
  llm_base_url = "https://llm.example.internal/v1"
  chat_models  = "qwen2.5:14b"
  embed_model  = "embeddinggemma"
  embed_dims   = 768
  rerank_url   = ""
}

network = {
  kube_api_cidrs = ["10.0.0.1/32"] # apiserver endpoint IPs (`kubectl get endpoints kubernetes`), never the Service ClusterIP
  llm_cidrs      = ["10.70.0.0/24"]
  oidc_cidrs     = ["10.71.0.0/24"]
}

storage = { postgres = "20Gi", elasticsearch = "50Gi", minio = "50Gi" }

# Secrets (create mode): override via TF_VAR_secret_data in CI. Example shape only.
# secret_data = {
#   db_password       = "..."    # openssl rand -hex 32
#   es_app_password   = "..."
#   minio_root_user   = "..."
#   minio_root_pass   = "..."
#   minio_app         = { access = "...", secret = "..." }
#   minio_pg_backup   = { access = "...", secret = "..." }
#   minio_es_snapshot = { access = "...", secret = "..." }
#   llm_api_key       = "..."    # required unless models.llm_key_used = false
# }
# pull_secret = { server = "registry.internal.example", username = "...", password = "..." }
