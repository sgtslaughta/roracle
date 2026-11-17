# kind apply rehearsal (deploy/terraform/test/kind-e2e.sh). Committed: no secrets (script passes TF_VAR_secret_data).
# The "air-gap mirror" is a local registry:2 reachable as localhost:5055 inside the kind nodes (containerd mirror).
kubeconfig_path    = "test/.tools/kubeconfig" # relative to deploy/terraform; never touches ~/.kube/config
kubeconfig_context = "kind-rorac-kind"

mirror_registry = "localhost:5055"
ha              = false # single instances, small resources
protect_data    = true  # the script proves destroy is blocked, then flips it with `state mv` (README Destroy cautions)

charts = {
  cnpg           = { repository = "oci://localhost:5055/charts", version = "0.26.0" }
  barman         = { repository = "oci://localhost:5055/charts", version = "0.9.0" }
  cert_manager   = { repository = "oci://localhost:5055/charts", version = "v1.17.0" }
  eck            = { repository = "oci://localhost:5055/charts", version = "2.16.0" }
  minio_operator = { repository = "oci://localhost:5055/charts", version = "7.0.0" }
}

operator_images = {
  cnpg           = { tag = "1.26.0" }
  barman         = { tag = "v0.16.0", sidecar_tag = "v0.16.0" }
  cert_manager   = { tag = "v1.17.0" }
  eck            = { tag = "2.16.0" }
  minio_operator = { tag = "v7.0.0" }
}

images = {
  registry    = "localhost:5055"
  backend_tag = "0.1.0" # built from this repo and pushed to the mirror by the script
  web_tag     = "0.1.0"
}

# quay.io/minio/{minio,mc} are no longer pullable (401): no bundled tenant in the rehearsal, a bitnamilegacy/minio stand-in
# (test/s3-stub.yaml, created by the script) serves S3 and the pod CIDR of kind is allowed as S3 egress (README, known gaps).
minio_mode = "external"
network    = { s3_cidrs = ["10.244.0.0/16"] }

extra_values_file = "environments/kind-values.yaml"

# No ingress controller, no Keycloak, no LLM in kind: auth/chat are not exercised (see README, Local rehearsal).
ingress = { enabled = false }
keycloak = {
  issuer = "https://keycloak.kind.invalid/realms/research"
}
models = {
  llm_base_url = "http://llm.kind.invalid/v1"
  chat_models  = "kind-none"
  embed_model  = "kind-none"
  embed_dims   = 768
  llm_key_used = false
}

storage = { postgres = "2Gi", elasticsearch = "2Gi", minio = "1Gi" }

helm_timeout_s = 1200
