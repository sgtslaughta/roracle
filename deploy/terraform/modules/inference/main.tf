# OPTIONAL, disabled by default. In-cluster GPU inference placeholder:
#   llm    = OpenAI-spec server (vLLM-style; also serves embeddings if the model supports it)
#   rerank = HuggingFace text-embeddings-inference serving bge-reranker-v2-m3 (Cohere-style /v1/rerank)
# Images and model files come from the air-gap mirror; models are pre-loaded on a PVC (no runtime downloads).
terraform {
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes" }
  }
}

variable "namespace" {
  type = string
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

locals {
  port = 8000
  components = {
    llm = {
      cfg  = var.llm
      args = concat(["--model", var.llm.model_path, "--served-model-name", var.llm.served_name, "--port", tostring(local.port)], var.llm.args)
    }
    rerank = {
      cfg  = var.rerank
      args = concat(["--model-id", var.rerank.model_path, "--port", tostring(local.port)], var.rerank.args)
    }
  }
}

resource "kubernetes_deployment_v1" "this" {
  for_each = local.components
  metadata {
    name      = "inference-${each.key}"
    namespace = var.namespace
    labels    = { app = "inference-${each.key}" }
  }
  spec {
    replicas = 1
    selector {
      match_labels = { app = "inference-${each.key}" }
    }
    template {
      metadata {
        labels = { app = "inference-${each.key}" }
      }
      spec {
        node_selector = each.value.cfg.node_selector
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
          resources {
            requests = { cpu = each.value.cfg.cpu, memory = each.value.cfg.memory }
            limits   = { memory = each.value.cfg.memory, "nvidia.com/gpu" = tostring(each.value.cfg.gpus) }
          }
          volume_mount {
            name       = "models"
            mount_path = "/models"
            read_only  = true
          }
          readiness_probe {
            http_get {
              path = "/health"
              port = local.port
            }
            period_seconds    = 10
            failure_threshold = 60
          }
          security_context {
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }
        }
        volume {
          name = "models"
          persistent_volume_claim {
            claim_name = each.value.cfg.models_pvc
            read_only  = true
          }
        }
      }
    }
  }
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

output "endpoints" {
  description = "In-cluster URLs for ROR_LLM_BASE_URL and ROR_RERANK_URL. Add the inference namespace to networkPolicy egress (llm cidrs/selectors)."
  value = {
    llm    = "http://inference-llm.${var.namespace}.svc:${local.port}/v1"
    rerank = "http://inference-rerank.${var.namespace}.svc:${local.port}/v1/rerank"
  }
}
