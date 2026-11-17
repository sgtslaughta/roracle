# rook-ceph object storage glue (the operator and cluster charts are installed by modules/operators):
# ObjectBucketClaims for the three buckets -> wait until bound -> read the generated credentials (output) -> NetworkPolicy
# opening api/worker/migrate and the ES/PG pods to RGW. Not exercised on a live Ceph in the kind rehearsal (no raw disks).
terraform {
  required_providers {
    kubernetes = { source = "hashicorp/kubernetes" }
    helm       = { source = "hashicorp/helm" }
  }
}

variable "namespace" {
  description = "App namespace (OBCs and their credential Secrets live here)."
  type        = string
}

variable "ceph_namespace" {
  type = string
}

variable "store_name" {
  type = string
}

variable "storage_class" {
  description = "Bucket StorageClass created by the rook-ceph-cluster chart."
  type        = string
}

variable "buckets" {
  description = "key (app|pg_backup|es_snapshot) => bucket name."
  type        = map(string)
}

variable "tls" {
  type    = bool
  default = false
}

variable "kubeconfig_path" {
  type = string
}

variable "kubeconfig_context" {
  type    = string
  default = null
}

variable "timeout" {
  type    = string
  default = "1800s"
}

locals {
  prefix = "roracle"
  obc    = { for k in keys(var.buckets) : k => "${local.prefix}-${replace(k, "_", "-")}" }
}

resource "helm_release" "buckets" {
  name      = "roracle-buckets"
  namespace = var.namespace
  chart     = "${path.module}/obc"
  values = [yamlencode({
    prefix       = local.prefix
    storageClass = var.storage_class
    buckets      = var.buckets
  })]
  wait = true
}

# OBCs bind only once RGW serves; the provisioner then writes Secret/ConfigMap <obc-name> in the app namespace.
resource "terraform_data" "bound" {
  input      = helm_release.buckets.id
  depends_on = [helm_release.buckets]
  provisioner "local-exec" {
    command = join(" ", compact([
      "kubectl --kubeconfig '${pathexpand(var.kubeconfig_path)}'",
      var.kubeconfig_context == null ? "" : "--context '${var.kubeconfig_context}'",
      "-n '${var.namespace}' wait --for=jsonpath='{.status.phase}'=Bound objectbucketclaim --all --timeout=${var.timeout}",
    ]))
  }
}

data "kubernetes_secret_v1" "cred" {
  for_each = local.obc
  metadata {
    name      = each.value
    namespace = var.namespace
  }
  depends_on = [terraform_data.bound]
}

# Additive allow: the chart's default-deny egress would otherwise block RGW (in-cluster, so it is not covered by s3Cidrs).
resource "kubernetes_network_policy_v1" "rgw_egress" {
  metadata {
    name      = "roracle-rgw-egress"
    namespace = var.namespace
  }
  spec {
    pod_selector {
      match_expressions {
        key      = "app.kubernetes.io/component"
        operator = "In"
        values   = ["api", "worker", "migrate"]
      }
    }
    policy_types = ["Egress"]
    egress {
      to {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = var.ceph_namespace }
        }
        pod_selector {
          match_labels = { app = "rook-ceph-rgw", rook_object_store = var.store_name }
        }
      }
      ports {
        port     = var.tls ? 443 : 80
        protocol = "TCP"
      }
      ports {
        port     = var.tls ? 8443 : 8080 # RGW container ports behind the Service (post-DNAT)
        protocol = "TCP"
      }
    }
  }
}

output "credentials" {
  description = "key => {access, secret} from the OBC Secrets."
  sensitive   = true
  value = { for k, d in data.kubernetes_secret_v1.cred : k => {
    access = d.data["AWS_ACCESS_KEY_ID"]
    secret = d.data["AWS_SECRET_ACCESS_KEY"]
  } }
}

output "endpoint" {
  value = "${var.tls ? "https" : "http"}://rook-ceph-rgw-${var.store_name}.${var.ceph_namespace}.svc:${var.tls ? 443 : 80}"
}
