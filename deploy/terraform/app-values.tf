# Typed variables -> Helm values (yamlencode). `extra_values_file` is merged after this by the app module.
locals {
  app_fullname = strcontains(var.release_name, "roracle") ? var.release_name : "${var.release_name}-roracle"

  llm_url = var.models.llm_base_url != "" ? var.models.llm_base_url : (
    local.inference_enabled ? module.inference[0].endpoints.llm : ""
  )
  rerank_url = var.models.rerank_url != "" ? var.models.rerank_url : (
    local.inference_enabled ? module.inference[0].endpoints.rerank : ""
  )

  # Env values that are empty are omitted so chart / backend defaults apply.
  config_raw = {
    ROR_OIDC_ISSUER     = var.keycloak.issuer
    ROR_OIDC_JWKS_URL   = var.keycloak.jwks_url != "" ? var.keycloak.jwks_url : "${var.keycloak.issuer}/protocol/openid-connect/certs"
    ROR_OIDC_AUDIENCE   = var.keycloak.audience
    ROR_LLM_BASE_URL    = local.llm_url
    ROR_CHAT_MODELS     = var.models.chat_models
    ROR_EMBED_MODEL     = var.models.embed_model
    ROR_EMBED_DIMS      = var.models.embed_dims == null ? "" : tostring(var.models.embed_dims)
    ROR_EMBED_TOKENIZER = var.models.tokenizer
    ROR_RERANK_URL      = local.rerank_url
    ROR_RERANK_MODEL    = var.models.rerank_model != "" ? var.models.rerank_model : (local.inference_enabled ? var.inference.rerank.served_name : "")
    ROR_PUBLIC_BASE_URL = var.ingress.host == "" ? "" : "https://${var.ingress.host}"
  }
  s3_endpoint = local.ceph_on ? module.ceph[0].endpoint : var.s3.endpoint
  s3_https    = startswith(local.s3_endpoint, "https://")
  s3_hostport = trimprefix(trimprefix(local.s3_endpoint, "https://"), "http://")
  s3_ca_obj   = local.ceph_on && var.ceph.rgw.cert_manager_issuer != "" ? try(module.ceph[0].ca_secret, null) : var.s3.ca_secret
  s3_ca       = local.s3_ca_obj != null ? [local.s3_ca_obj] : []
  config = { for k, v in merge(local.config_raw, var.models.extra_env, {
    ROR_S3_ENDPOINT = local.s3_endpoint
    ROR_S3_BUCKET   = var.s3.bucket
  }) : k => v if v != "" }

  # Dev profile (ha=false): single instances, small requests. Prod (ha=true): chart defaults.
  dev = !var.ha
  r   = { for k, v in var.replicas : k => v if v != null }

  dev_values = {
    topologySpread = { enabled = false }
    api = { replicas = 1, autoscaling = { enabled = false }, pdb = { enabled = false }
    resources = { requests = { cpu = "500m", memory = "1Gi" }, limits = { memory = "2Gi" } } }
    worker = { replicas = 1, autoscaling = { enabled = false }, pdb = { enabled = false }, tmpSize = "10Gi"
    resources = { requests = { cpu = "1", memory = "2Gi" }, limits = { memory = "4Gi" } } }
    web = { replicas = 1, pdb = { enabled = false } }
    postgres = { instances = 1
    resources = { requests = { cpu = "500m", memory = "1Gi" }, limits = { memory = "2Gi" } } }
    elasticsearch = {
      nodeSets  = [{ name = "default", count = 1, roles = ["master", "data", "ingest"] }]
      resources = { requests = { cpu = "1", memory = "2Gi" }, limits = { memory = "2Gi" } }
    }
    parsers = {
      gotenberg      = { replicas = 1 }
      draftgotenberg = { replicas = 1 }
      docling        = { replicas = 1, resources = { requests = { cpu = "2", memory = "4Gi" }, limits = { memory = "8Gi" } } }
      grobid = { replicas = 1, env = { JAVA_TOOL_OPTIONS = "-XX:-UseContainerSupport -Xmx2304m" }
      resources = { requests = { cpu = "1", memory = "2Gi" }, limits = { memory = "3Gi" } } }
      clamav = { replicas = 1 }
      pdb    = { enabled = false }
    }
  }

  base_values = {
    global           = { imageRegistry = coalesce(var.images.registry, var.mirror_registry) }
    imagePullSecrets = nonsensitive(var.pull_secret == null ? [] : [{ name = var.pull_secret.name }])
    image = {
      backend = { tag = var.images.backend_tag, digest = var.images.backend_digest, pullPolicy = var.images.pull_policy }
      web     = { tag = var.images.web_tag, digest = var.images.web_digest, pullPolicy = var.images.pull_policy }
    }
    secrets = { existingSecret = var.secret_names.app, llmApiKeyEnabled = var.models.llm_key_used, s3Secret = local.ceph_on ? var.secret_names.s3 : "" }
    config  = local.config
    web     = { oidcClientId = var.keycloak.web_client_id }
    ingress = {
      enabled       = var.ingress.enabled
      className     = var.ingress.class_name
      host          = var.ingress.host
      tlsSecretName = var.ingress.tls_secret
    }
    postgres = {
      mode                 = var.postgres_mode
      storage              = { size = var.storage.postgres, storageClass = var.storage.class }
      appCredentialsSecret = var.secret_names.pg_app
      backup = {
        enabled           = var.backups_enabled.postgres
        credentialsSecret = { name = var.secret_names.pg_backup }
        destinationPath   = "s3://${var.s3.pg_backup_bucket}/"
        endpointURL       = local.s3_endpoint
        endpointCA        = length(local.s3_ca) == 0 ? {} : local.s3_ca[0]
      }
    }
    elasticsearch = {
      mode          = var.elasticsearch_mode
      storage       = { size = var.storage.elasticsearch, storageClass = var.storage.class }
      appUserSecret = var.secret_names.es_app
      snapshot = {
        enabled           = var.backups_enabled.elasticsearch
        credentialsSecret = var.secret_names.es_snapshot
        bucket            = var.s3.es_snapshot_bucket
        endpoint          = local.s3_hostport
        protocol          = local.s3_https ? "https" : "http"
        extraCASecret     = length(local.s3_ca) == 0 ? {} : local.s3_ca[0]
      }
    }
    caBundle = { extraSecrets = local.s3_ca }
    networkPolicy = {
      operatorNamespaces       = { eck = var.namespaces.eck, cnpg = var.namespaces.cnpg }
      monitoringNamespace      = var.namespaces.monitoring
      ingressNamespaceSelector = { "kubernetes.io/metadata.name" = var.network.ingress_namespace }
      kubeAPI                  = { cidrs = var.network.kube_api_cidrs, ports = [443, 6443] }
      # object storage egress for api/worker/migrate and the CNPG/ES data tier: external CIDRs, or the in-cluster RGW pods (post-DNAT port)
      s3 = {
        cidrs             = local.ceph_on ? [] : var.network.s3_cidrs
        ports             = local.ceph_on ? [local.rgw_tls ? 443 : 80] : var.network.s3_ports
        namespaceSelector = local.ceph_on ? { "kubernetes.io/metadata.name" = var.namespaces.rook_ceph } : {}
        podSelector       = local.ceph_on ? { app = "rook-ceph-rgw", rook_object_store = var.ceph.store_name } : {}
      }
      externalCIDRs = {
        postgres      = { cidrs = var.network.postgres_cidrs, ports = [5432] }
        elasticsearch = { cidrs = var.network.elastic_cidrs, ports = [9200] }
      }
      egressCIDRs = {
        llm = {
          cidrs = var.network.llm_cidrs
          ports = local.inference_enabled ? distinct(concat(var.network.llm_ports, [8000])) : var.network.llm_ports
          # in-cluster inference: whole namespace (its own NetworkPolicy admits only api/worker); selectors apply after DNAT
          namespaceSelector = local.inference_enabled ? { "kubernetes.io/metadata.name" = var.namespaces.inference } : var.network.llm_namespace_selector
          podSelector       = local.inference_enabled ? {} : var.network.llm_pod_selector
        }
        oidc = { cidrs = var.network.oidc_cidrs, ports = [443] }
      }
    }
  }

  replica_values = {
    api    = lookup(local.r, "api", null) == null ? {} : { replicas = local.r.api, autoscaling = { minReplicas = local.r.api } }
    worker = lookup(local.r, "worker", null) == null ? {} : { replicas = local.r.worker, autoscaling = { minReplicas = local.r.worker } }
    web    = lookup(local.r, "web", null) == null ? {} : { replicas = local.r.web }
  }

  # Separate YAML documents: Helm deep-merges them in order (later wins), unlike Terraform merge().
  app_values_docs = compact([
    yamlencode(local.base_values),
    local.dev ? yamlencode(local.dev_values) : "",
    yamlencode(local.replica_values),
  ])
}
