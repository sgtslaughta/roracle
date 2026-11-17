# rook-ceph object storage glue (the operator and cluster charts are installed by modules/operators):
# ObjectBucketClaims for the three buckets -> wait until bound -> read the generated credentials -> chart Secrets (always) -> RGW ingress policy,
# optional cert-manager certificate for RGW. Not exercised on a live Ceph in the kind rehearsal (no raw disks).
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

variable "tls_secret" {
  description = "RGW certificate Secret name in the Ceph namespace (\"\" = plain HTTP, test clusters only)."
  type        = string
  default     = ""
}

variable "cert_manager_issuer" {
  type    = string
  default = ""
}

variable "secret_names" {
  description = "{s3, pg_backup, es_snapshot} Secret names consumed by the chart."
  type        = map(string)
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
  tls    = var.tls_secret != ""
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

# RGW ingress: only the app namespace (api/worker/migrate, CNPG barman, ES snapshots) and the Ceph namespace itself (operator, mgr,
# OBC provisioner). Egress from the app side is the chart's networkPolicy.s3 (Terraform sets it to the RGW pods).
resource "kubernetes_network_policy_v1" "rgw_ingress" {
  metadata {
    name      = "roracle-rgw-ingress"
    namespace = var.ceph_namespace
  }
  spec {
    pod_selector {
      match_labels = { app = "rook-ceph-rgw", rook_object_store = var.store_name }
    }
    policy_types = ["Ingress"]
    ingress {
      from {
        namespace_selector {
          match_labels = { "kubernetes.io/metadata.name" = var.namespace }
        }
      }
      from {
        pod_selector {}
      }
      ports {
        port     = local.tls ? 8443 : 8080 # RGW container port behind the Service (post-DNAT)
        protocol = "TCP"
      }
    }
  }
}

# ---- TLS via cert-manager (optional): certificate for the RGW service + CA copied to the app namespace ----
resource "helm_release" "rgw_tls" {
  count     = var.cert_manager_issuer == "" ? 0 : 1
  name      = "roracle-rgw-tls"
  namespace = var.ceph_namespace
  chart     = "${path.module}/tls"
  values = [yamlencode({
    issuer     = var.cert_manager_issuer
    secretName = var.tls_secret
    dnsNames = [
      "rook-ceph-rgw-${var.store_name}.${var.ceph_namespace}.svc",
      "rook-ceph-rgw-${var.store_name}.${var.ceph_namespace}.svc.cluster.local",
      "rook-ceph-rgw-${var.store_name}.${var.ceph_namespace}",
    ]
  })]
  wait = true
}

resource "terraform_data" "cert_ready" {
  count      = var.cert_manager_issuer == "" ? 0 : 1
  input      = helm_release.rgw_tls[0].id
  depends_on = [helm_release.rgw_tls]
  provisioner "local-exec" {
    command = join(" ", compact([
      "kubectl --kubeconfig '${pathexpand(var.kubeconfig_path)}'",
      var.kubeconfig_context == null ? "" : "--context '${var.kubeconfig_context}'",
      "-n '${var.ceph_namespace}' wait --for=condition=Ready certificate/${var.tls_secret} --timeout=${var.timeout}",
    ]))
  }
}

data "kubernetes_secret_v1" "rgw_tls" {
  count = var.cert_manager_issuer == "" ? 0 : 1
  metadata {
    name      = var.tls_secret
    namespace = var.ceph_namespace
  }
  depends_on = [terraform_data.cert_ready]
}

resource "kubernetes_secret_v1" "s3_ca" {
  count = var.cert_manager_issuer == "" ? 0 : 1
  metadata {
    name      = "roracle-s3-ca"
    namespace = var.namespace
  }
  data = { "ca.crt" = data.kubernetes_secret_v1.rgw_tls[0].data["ca.crt"] }
}

# ---- S3 Secrets the chart consumes (created in every secrets_mode: they hold only OBC output, no operator secret) ----
resource "kubernetes_secret_v1" "app_s3" {
  metadata {
    name      = var.secret_names.s3
    namespace = var.namespace
  }
  data = {
    ROR_S3_ACCESS_KEY = data.kubernetes_secret_v1.cred["app"].data["AWS_ACCESS_KEY_ID"]
    ROR_S3_SECRET_KEY = data.kubernetes_secret_v1.cred["app"].data["AWS_SECRET_ACCESS_KEY"]
  }
}

resource "kubernetes_secret_v1" "pg_backup" {
  metadata {
    name      = var.secret_names.pg_backup
    namespace = var.namespace
  }
  data = {
    ACCESS_KEY_ID     = data.kubernetes_secret_v1.cred["pg_backup"].data["AWS_ACCESS_KEY_ID"]
    ACCESS_SECRET_KEY = data.kubernetes_secret_v1.cred["pg_backup"].data["AWS_SECRET_ACCESS_KEY"]
  }
}

resource "kubernetes_secret_v1" "es_snapshot" {
  metadata {
    name      = var.secret_names.es_snapshot
    namespace = var.namespace
  }
  data = {
    "s3.client.default.access_key" = data.kubernetes_secret_v1.cred["es_snapshot"].data["AWS_ACCESS_KEY_ID"]
    "s3.client.default.secret_key" = data.kubernetes_secret_v1.cred["es_snapshot"].data["AWS_SECRET_ACCESS_KEY"]
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
  value = "${local.tls ? "https" : "http"}://rook-ceph-rgw-${var.store_name}.${var.ceph_namespace}.svc:${local.tls ? 443 : 80}"
}

output "ca_secret" {
  description = "{name, key} of the CA Secret created in the app namespace (cert_manager_issuer mode), else null."
  value       = var.cert_manager_issuer == "" ? null : { name = kubernetes_secret_v1.s3_ca[0].metadata[0].name, key = "ca.crt" }
}
