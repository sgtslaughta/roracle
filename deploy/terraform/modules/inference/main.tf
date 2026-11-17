# OPTIONAL, disabled by default. In-cluster GPU inference placeholder:
#   llm    = OpenAI-spec server (vLLM-style; also serves embeddings if the model supports it), API key required
#   rerank = engine "vllm" (default, `--task score`, Cohere/Jina-style POST /v1/rerank = what the backend sends) or
#            engine "tei" (HF text-embeddings-inference: native POST /rerank {query,texts}; NOT wire-compatible with the
#            backend today, so it needs a backend adapter first)
# Images and model files come from the air-gap mirror; models are pre-loaded on a PVC (no runtime downloads).
# Access control: default-deny NetworkPolicy, only the rOracle api/worker pods may connect; no egress except DNS.
terraform {
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes" }
  }
}

variable "namespace" {
  type = string
}

variable "app_namespace" {
  type = string
}

variable "release_name" {
  description = "Helm release name of rOracle (pod label app.kubernetes.io/instance)."
  type        = string
}

variable "pull_secret_names" {
  type    = list(string)
  default = []
}

variable "llm" {
  type = any
}

variable "rerank" {
  type = any
}

variable "create_keys_secret" {
  description = "Create Secret inference-keys from the key variables (secrets_mode=create). false: it must already exist in the inference namespace with keys LLM_API_KEY (and RERANK_API_KEY when rerank.api_key_enabled)."
  type        = bool
  default     = true
}

variable "llm_api_key" {
  description = "Same value as the app's ROR_LLM_API_KEY."
  type        = string
  default     = ""
  sensitive   = true
}

variable "rerank_api_key" {
  type      = string
  default   = ""
  sensitive = true
}

locals {
  port     = 8000
  keys_sec = "inference-keys"
  tei      = var.rerank.engine == "tei"
  components = {
    llm = {
      cfg     = var.llm
      args    = concat(["--model", var.llm.model_path, "--served-model-name", var.llm.served_name, "--port", tostring(local.port)], var.llm.args)
      key_env = { name = "VLLM_API_KEY", key = "LLM_API_KEY" } # vLLM reads the key from env (not on the command line)
    }
    rerank = {
      cfg = var.rerank
      args = local.tei ? concat(["--model-id", var.rerank.model_path, "--port", tostring(local.port)], local.rerank_extra) : concat(
        ["--model", var.rerank.model_path, "--served-model-name", var.rerank.served_name, "--port", tostring(local.port)], local.rerank_extra
      )
      key_env = var.rerank.api_key_enabled ? { name = local.tei ? "API_KEY" : "VLLM_API_KEY", key = "RERANK_API_KEY" } : null
    }
  }
  home = "/home/inference"
  # null args = engine default. Newer vLLM auto-detects the score model / uses `--runner pooling`: override per mirrored version.
  rerank_extra = try(var.rerank.args, null) != null ? var.rerank.args : (local.tei ? [] : ["--task", "score"])
}

resource "kubernetes_secret_v1" "keys" {
  count = var.create_keys_secret ? 1 : 0
  metadata {
    name      = local.keys_sec
    namespace = var.namespace
  }
  data = merge(
    { LLM_API_KEY = var.llm_api_key },
    var.rerank.api_key_enabled ? { RERANK_API_KEY = var.rerank_api_key } : {},
  )
  lifecycle {
    precondition {
      condition     = nonsensitive(var.llm_api_key != "")
      error_message = "secret_data.llm_api_key is required for the inference module (the LLM server enforces it and rOracle sends it)."
    }
  }
}

resource "kubernetes_deployment_v1" "this" {
  for_each = local.components

  wait_for_rollout = true
  timeouts {
    create = "30m" # large models take long to load
    update = "30m"
  }

  metadata {
    name      = "inference-${each.key}"
    namespace = var.namespace
    labels    = { app = "inference-${each.key}" }
  }
  spec {
    replicas = 1
    strategy {
      type = "Recreate" # 1 GPU per pod: a rolling update would need a spare GPU
    }
    selector {
      match_labels = { app = "inference-${each.key}" }
    }
    template {
      metadata {
        labels = { app = "inference-${each.key}" }
      }
      spec {
        node_selector                   = each.value.cfg.node_selector
        automount_service_account_token = false
        security_context {
          run_as_non_root = true
          run_as_user     = each.value.cfg.run_as_user
          fs_group        = each.value.cfg.run_as_user
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }
        dynamic "image_pull_secrets" {
          for_each = var.pull_secret_names
          content {
            name = image_pull_secrets.value
          }
        }
        dynamic "toleration" {
          for_each = each.value.cfg.tolerations
          content {
            key      = toleration.value.key
            operator = toleration.value.operator
            value    = toleration.value.value
            effect   = toleration.value.effect
          }
        }
        container {
          name  = each.key
          image = each.value.cfg.image
          args  = each.value.args
          port {
            container_port = local.port
          }
          env {
            name  = "HOME"
            value = local.home
          }
          env {
            name  = "HF_HUB_OFFLINE"
            value = "1"
          }
          env {
            name  = "TRANSFORMERS_OFFLINE"
            value = "1"
          }
          dynamic "env" {
            for_each = each.value.key_env == null ? [] : [each.value.key_env]
            content {
              name = env.value.name
              value_from {
                secret_key_ref {
                  name = local.keys_sec
                  key  = env.value.key
                }
              }
            }
          }
          resources {
            requests = { cpu = each.value.cfg.cpu, memory = each.value.cfg.memory }
            limits   = { memory = each.value.cfg.memory, "nvidia.com/gpu" = tostring(each.value.cfg.gpus) }
          }
          volume_mount {
            name       = "models"
            mount_path = "/models"
            read_only  = true
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
          volume_mount {
            name       = "home"
            mount_path = local.home # ~/.cache
          }
          volume_mount {
            name       = "shm"
            mount_path = "/dev/shm"
          }
          readiness_probe {
            http_get {
              path = "/health"
              port = local.port
            }
            period_seconds    = 10
            failure_threshold = 90
          }
          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            run_as_non_root            = true
            capabilities {
              drop = ["ALL"]
            }
          }
        }
        volume {
          name = "models"
          persistent_volume_claim {
            claim_name = each.value.cfg.models_pvc # needs ReadOnlyMany/ReadWriteMany if the pods land on different nodes
            read_only  = true
          }
        }
        volume {
          name = "tmp"
          empty_dir {}
        }
        volume {
          name = "home"
          empty_dir {}
        }
        volume {
          name = "shm"
          empty_dir {
            medium     = "Memory"
            size_limit = "8Gi"
          }
        }
      }
    }
  }
  depends_on = [kubernetes_secret_v1.keys]
}

resource "kubernetes_service_v1" "this" {
  for_each = local.components
  metadata {
    name      = "inference-${each.key}"
    namespace = var.namespace
  }
  spec {
    selector = { app = "inference-${each.key}" }
    port {
      port        = local.port
      target_port = local.port
    }
  }
}

# Default-deny both directions; allow only rOracle api/worker pods in (same shape as the chart's own selectors) and DNS out.
resource "kubernetes_network_policy_v1" "deny_all" {
  metadata {
    name      = "default-deny"
    namespace = var.namespace
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy_v1" "allow_app" {
  metadata {
    name      = "allow-roracle-ingress"
    namespace = var.namespace
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress"]
    ingress {
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = var.app_namespace }
        }
        pod_selector {
          match_labels = { "app.kubernetes.io/name" = "roracle", "app.kubernetes.io/instance" = var.release_name }
          match_expressions {
            key      = "app.kubernetes.io/component"
            operator = "In"
            values   = ["api", "worker"]
          }
        }
      }
      ports {
        port     = tostring(local.port)
        protocol = "TCP"
      }
    }
  }
}

resource "kubernetes_network_policy_v1" "allow_dns" {
  metadata {
    name      = "allow-dns-egress"
    namespace = var.namespace
  }
  spec {
    pod_selector {}
    policy_types = ["Egress"]
    egress {
      to {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = "kube-system" }
        }
      }
      ports {
        port     = "53"
        protocol = "UDP"
      }
      ports {
        port     = "53"
        protocol = "TCP"
      }
    }
  }
}

output "endpoints" {
  description = "In-cluster URLs for ROR_LLM_BASE_URL and ROR_RERANK_URL (the root module also opens the app NetworkPolicy egress to this namespace, port 8000)."
  value = {
    llm    = "http://inference-llm.${var.namespace}.svc:${local.port}/v1"
    rerank = "http://inference-rerank.${var.namespace}.svc:${local.port}${local.tei ? "/rerank" : "/v1/rerank"}"
  }
}
