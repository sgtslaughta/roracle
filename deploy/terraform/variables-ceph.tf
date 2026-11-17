# ---- Object storage: Rook-Ceph (RGW) or an existing S3 ----
variable "object_store" {
  description = <<-EOT
    rook-ceph: Terraform installs the rook-ceph operator + rook-ceph-cluster charts (CephObjectStore/RGW) and creates the buckets with ObjectBucketClaims; credentials flow into the app Secrets.
    Needs >= 3 nodes with raw disks (README, Ceph prerequisites). external: an S3 service you operate (set s3.endpoint and secret_data.s3_*); recommended for small installs and dev/kind.
  EOT
  type        = string
  default     = "external"
  validation {
    condition     = contains(["rook-ceph", "external"], var.object_store)
    error_message = "rook-ceph | external"
  }
}

variable "s3" {
  description = "S3 layout. endpoint (external only; rook-ceph derives the RGW service) e.g. https://s3.internal:9000. Buckets: app data, CNPG backups, ES snapshots (the backup buckets only matter while postgres.backup / elasticsearch.snapshot are enabled)."
  type = object({
    endpoint           = optional(string, "")
    bucket             = optional(string, "roracle")
    pg_backup_bucket   = optional(string, "roracle-pg-backup")
    es_snapshot_bucket = optional(string, "roracle-es-snapshots")
    ca_secret          = optional(object({ name = string, key = optional(string, "ca.crt") }))
  })
  default = {}
}

variable "ceph" {
  description = <<-EOT
    rook-ceph settings. image = Ceph image (default <mirror_registry>/ceph/ceph, tag a gated placeholder; e.g. v19.2.3).
    OSDs: never "all devices": set device_filter (regex on device names) and/or nodes ([{name = "<k8s hostname>", devices = [{name = "sdb"}]}]); empty = gated placeholder.
    data_pool: erasure (data_chunks + coding_chunks, needs >= that many failure domains) or replicated; metadata is always replicated.
    rgw.tls_secret: Secret (in the Ceph namespace, with tls.crt/tls.key/ca.crt) enabling HTTPS 443 on RGW; also set s3.ca_secret = {name} (copy of the CA in the app namespace) so the app trusts it.
  EOT
  type = object({
    image                        = optional(object({ repository = optional(string), tag = optional(string) }), {})
    store_name                   = optional(string, "roracle")
    mon_count                    = optional(number, 3)
    mgr_count                    = optional(number, 2)
    allow_multiple_mons_per_node = optional(bool, false) # true only for test clusters (data loss on node failure)
    failure_domain               = optional(string, "host")
    device_filter                = optional(string, "")
    nodes                        = optional(list(any), [])
    metadata_replicas            = optional(number, 3)
    data_pool = optional(object({
      mode          = optional(string, "erasure")
      data_chunks   = optional(number, 2)
      coding_chunks = optional(number, 1)
      replicas      = optional(number, 3)
    }), {})
    rgw = optional(object({
      instances  = optional(number, 2)
      tls_secret = optional(string, "")
      resources  = optional(any, { requests = { cpu = "1", memory = "2Gi" }, limits = { memory = "4Gi" } })
    }), {})
    resources = optional(any, {
      mon = { requests = { cpu = "500m", memory = "1Gi" }, limits = { memory = "2Gi" } }
      mgr = { requests = { cpu = "500m", memory = "512Mi" }, limits = { memory = "1Gi" } }
      osd = { requests = { cpu = "1", memory = "4Gi" }, limits = { memory = "6Gi" } }
    })
    obc_timeout = optional(string, "1800s") # kubectl wait for the ObjectBucketClaims to bind (RGW must be up)
  })
  default = {}
  validation {
    condition     = contains(["erasure", "replicated"], var.ceph.data_pool.mode)
    error_message = "ceph.data_pool.mode: erasure | replicated"
  }
}
