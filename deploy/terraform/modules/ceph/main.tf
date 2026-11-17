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

variable "image" {
  description = "Image of the bucket-setup Job: the rOracle backend image (python + boto3), from the mirror."
  type        = string
}

variable "pull_secret_name" {
  type    = string
  default = ""
}

variable "object_lock" {
  description = "Default retention on the PG backup and ES snapshot buckets (S3 object lock). Keep >= the chart's backup/snapshot retention (30 d)."
  type = object({
    mode           = optional(string, "GOVERNANCE")
    retention_days = optional(number, 30)
  })
  default = {}
}

locals {
  tls    = var.tls_secret != ""
  prefix = "roracle"
  # app bucket: OBC (owner = the OBC user, tmp/ lifecycle). Backup buckets: created locked by the Job below, writers are CephObjectStoreUsers.
  obc = { app = "${local.prefix}-app" }
  users = {
    admin       = "roracle-admin"
    pg_backup   = "roracle-pg-backup"
    es_snapshot = "roracle-es-snapshot"
  }
  rgw_endpoint = "${local.tls ? "https" : "http"}://rook-ceph-rgw-${var.store_name}.${var.ceph_namespace}.svc:${local.tls ? 443 : 80}"
  # tmp/ staging leftovers and stale multipart uploads expire after 1 d (the former minio-policy hook did this)
  app_lifecycle = {
    Rules = [
      { ID = "tmp-expire", Status = "Enabled", Filter = { Prefix = "tmp/" }, Expiration = { Days = 1 } },
      { ID = "abort-multipart", Status = "Enabled", Filter = { Prefix = "" }, AbortIncompleteMultipartUpload = { DaysAfterInitiation = 1 } },
    ]
  }
  backup_buckets = [
    { name = var.buckets["pg_backup"], user = local.users.pg_backup },
    { name = var.buckets["es_snapshot"], user = local.users.es_snapshot },
  ]
  setup_script = file("${path.module}/bucket-setup.py")
}

resource "helm_release" "buckets" {
  name      = "roracle-buckets"
  namespace = var.namespace
  chart     = "${path.module}/obc"
  values = [yamlencode({
    prefix       = local.prefix
    storageClass = var.storage_class
    buckets      = { app = var.buckets["app"] }
    lifecycle    = local.app_lifecycle
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

# ---- Backup buckets: object lock + default retention + bucket policy, created by a Job as the admin object-store user ----
resource "helm_release" "users" {
  name      = "roracle-rgw-users"
  namespace = var.ceph_namespace
  chart     = "${path.module}/users"
  values    = [yamlencode({ store = var.store_name, users = values(local.users) })]
  wait      = true
}

resource "terraform_data" "users_ready" {
  input      = helm_release.users.id
  depends_on = [helm_release.users, terraform_data.bound]
  provisioner "local-exec" {
    command = join(" ", compact([
      "kubectl --kubeconfig '${pathexpand(var.kubeconfig_path)}'",
      var.kubeconfig_context == null ? "" : "--context '${var.kubeconfig_context}'",
      "-n '${var.ceph_namespace}' wait --for=jsonpath='{.status.phase}'=Ready cephobjectstoreuser --all --timeout=${var.timeout}",
    ]))
  }
}

# Runs in the Ceph namespace (RGW ingress allows it; admin keys are read from Rook's Secret directly and never copied).
resource "kubernetes_job_v1" "buckets" {
  metadata {
    name      = "roracle-bucket-setup-${substr(sha1(jsonencode([local.backup_buckets, var.object_lock, var.image, local.setup_script, local.rgw_endpoint])), 0, 8)}"
    namespace = var.ceph_namespace
  }
  wait_for_completion = true
  timeouts {
    create = "30m"
  }
  spec {
    backoff_limit = 20
    template {
      metadata {
        labels = { app = "roracle-bucket-setup" }
      }
      spec {
        restart_policy                  = "OnFailure"
        automount_service_account_token = false
        dynamic "image_pull_secrets" {
          for_each = var.pull_secret_name == "" ? [] : [var.pull_secret_name]
          content {
            name = image_pull_secrets.value
          }
        }
        security_context {
          run_as_non_root = true
          run_as_user     = 10001
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }
        container {
          name    = "setup"
          image   = var.image
          command = ["python", "-c", local.setup_script]
          env {
            name  = "AWS_REQUEST_CHECKSUM_CALCULATION"
            value = "when_required" # boto3 >= 1.36 default CRC32 trailers can break some RGW releases
          }
          env {
            name  = "AWS_RESPONSE_CHECKSUM_VALIDATION"
            value = "when_required"
          }
          env {
            name  = "S3_ENDPOINT"
            value = local.rgw_endpoint
          }
          env {
            name  = "BUCKETS"
            value = jsonencode(local.backup_buckets)
          }
          env {
            name  = "RETENTION_DAYS"
            value = tostring(var.object_lock.retention_days)
          }
          env {
            name  = "RETENTION_MODE"
            value = var.object_lock.mode
          }
          env {
            name = "ADMIN_AK"
            value_from {
              secret_key_ref {
                name = "rook-ceph-object-user-${var.store_name}-${local.users.admin}"
                key  = "AccessKey"
              }
            }
          }
          env {
            name = "ADMIN_SK"
            value_from {
              secret_key_ref {
                name = "rook-ceph-object-user-${var.store_name}-${local.users.admin}"
                key  = "SecretKey"
              }
            }
          }
          dynamic "env" {
            for_each = local.tls ? [1] : []
            content {
              name  = "AWS_CA_BUNDLE"
              value = "/ca/ca.crt"
            }
          }
          dynamic "volume_mount" {
            for_each = local.tls ? [1] : []
            content {
              name       = "ca"
              mount_path = "/ca"
              read_only  = true
            }
          }
          volume_mount {
            name       = "tmp"
            mount_path = "/tmp"
          }
          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = true
            capabilities {
              drop = ["ALL"]
            }
          }
          resources {
            requests = { cpu = "50m", memory = "128Mi" }
            limits   = { memory = "256Mi" }
          }
        }
        dynamic "volume" {
          for_each = local.tls ? [1] : []
          content {
            name = "ca"
            secret {
              secret_name = var.tls_secret # needs ca.crt (cert-manager adds it; supplied secrets must include it)
            }
          }
        }
        volume {
          name = "tmp"
          empty_dir {
            size_limit = "16Mi"
          }
        }
      }
    }
  }
  depends_on = [terraform_data.users_ready, helm_release.rgw_tls, terraform_data.cert_ready, kubernetes_network_policy_v1.rgw_ingress]
}

data "kubernetes_secret_v1" "user" {
  for_each = { pg_backup = local.users.pg_backup, es_snapshot = local.users.es_snapshot }
  metadata {
    name      = "rook-ceph-object-user-${var.store_name}-${each.value}"
    namespace = var.ceph_namespace
  }
  depends_on = [kubernetes_job_v1.buckets]
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
  lifecycle {
    precondition {
      condition     = contains(keys(data.kubernetes_secret_v1.rgw_tls[0].data), "ca.crt")
      error_message = "The issued RGW certificate Secret has no ca.crt (third-party issuer?): use ceph.rgw.tls_secret with s3.ca_secret instead of cert_manager_issuer."
    }
  }
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
    ACCESS_KEY_ID     = data.kubernetes_secret_v1.user["pg_backup"].data["AccessKey"]
    ACCESS_SECRET_KEY = data.kubernetes_secret_v1.user["pg_backup"].data["SecretKey"]
  }
}

resource "kubernetes_secret_v1" "es_snapshot" {
  metadata {
    name      = var.secret_names.es_snapshot
    namespace = var.namespace
  }
  data = {
    "s3.client.default.access_key" = data.kubernetes_secret_v1.user["es_snapshot"].data["AccessKey"]
    "s3.client.default.secret_key" = data.kubernetes_secret_v1.user["es_snapshot"].data["SecretKey"]
  }
}

output "credentials" {
  description = "key => {access, secret}: app from the OBC Secret, backup writers from the Rook user Secrets."
  sensitive   = true
  value = {
    app         = { access = data.kubernetes_secret_v1.cred["app"].data["AWS_ACCESS_KEY_ID"], secret = data.kubernetes_secret_v1.cred["app"].data["AWS_SECRET_ACCESS_KEY"] }
    pg_backup   = { access = data.kubernetes_secret_v1.user["pg_backup"].data["AccessKey"], secret = data.kubernetes_secret_v1.user["pg_backup"].data["SecretKey"] }
    es_snapshot = { access = data.kubernetes_secret_v1.user["es_snapshot"].data["AccessKey"], secret = data.kubernetes_secret_v1.user["es_snapshot"].data["SecretKey"] }
  }
}

output "endpoint" {
  value = local.rgw_endpoint
}

output "ca_secret" {
  description = "{name, key} of the CA Secret created in the app namespace (cert_manager_issuer mode), else null."
  value       = var.cert_manager_issuer == "" ? null : { name = kubernetes_secret_v1.s3_ca[0].metadata[0].name, key = "ca.crt" }
}
