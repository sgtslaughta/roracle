locals {
  placeholder_repo    = "oci://MIRROR.example.com/charts"
  placeholder_version = "0.0.0-PLACEHOLDER"
  obs_dir             = "${path.module}/../observability"

  # Defaults per chart. Override any attribute through var.charts[<key>].
  chart_defaults = {
    cnpg                  = { enabled = true, chart = "cloudnative-pg", ns = var.namespaces.cnpg }
    barman                = { enabled = true, chart = "plugin-barman-cloud", ns = var.namespaces.cnpg }
    cert_manager          = { enabled = true, chart = "cert-manager", ns = var.namespaces.cert_manager }
    eck                   = { enabled = true, chart = "eck-operator", ns = var.namespaces.eck }
    rook_ceph             = { enabled = local.ceph_on, chart = "rook-ceph", ns = var.namespaces.rook_ceph }
    rook_ceph_cluster     = { enabled = local.ceph_on, chart = "rook-ceph-cluster", ns = var.namespaces.rook_ceph }
    kube_prometheus_stack = { enabled = false, chart = "kube-prometheus-stack", ns = var.namespaces.monitoring }
    loki                  = { enabled = false, chart = "loki", ns = var.namespaces.observability }
    otel_collector        = { enabled = false, chart = "opentelemetry-collector", ns = var.namespaces.observability }
  }

  ceph_on   = var.object_store == "rook-ceph"
  pull_name = nonsensitive(var.pull_secret == null ? "" : var.pull_secret.name)
  pull      = local.pull_name != ""

  # Operator image overrides (air-gap): var.operator_images[<key>] or <mirror_registry>/<upstream path> + placeholder tag.
  image_defaults = {
    cnpg         = "cloudnative-pg/cloudnative-pg"
    barman       = "cloudnative-pg/plugin-barman-cloud"
    cert_manager = "jetstack/cert-manager" # prefix: -controller, -webhook, -cainjector, -startupapicheck
    eck          = "eck/eck-operator"
    rook_ceph    = "rook/ceph" # operator image; Ceph itself: var.ceph.image
  }
  img = {
    for k, p in local.image_defaults : k => {
      repository = coalesce(try(var.operator_images[k].repository, null), "${var.mirror_registry}/${p}")
      tag        = coalesce(try(var.operator_images[k].tag, null), local.placeholder_version)
    }
  }
  ceph_image = {
    repository = coalesce(var.ceph.image.repository, "${var.mirror_registry}/ceph/ceph")
    tag        = coalesce(var.ceph.image.tag, local.placeholder_version)
  }
  app_chart_version = yamldecode(file("${path.module}/../helm/roracle/Chart.yaml")).appVersion
  # backup-bucket setup Job: the rOracle backend image from the mirror (python + boto3)
  backend_ref       = "${coalesce(var.images.registry, var.mirror_registry)}/roracle/backend${var.images.backend_digest != "" ? "@${var.images.backend_digest}" : ":${coalesce(var.images.backend_tag, local.app_chart_version)}"}"
  rgw_tls           = var.ceph.rgw.tls_secret != "" || var.ceph.rgw.cert_manager_issuer != ""
  rgw_tls_secret    = var.ceph.rgw.cert_manager_issuer != "" ? "${var.ceph.store_name}-rgw-tls" : var.ceph.rgw.tls_secret
  ceph_bucket_class = "roracle-ceph-bucket"
  # S3 identities: rook-ceph = one OBC user per bucket; external = secret_data (backup identities default to the app one)
  s3_ext = {
    app         = { access = var.secret_data.s3_access_key, secret = var.secret_data.s3_secret_key }
    pg_backup   = var.secret_data.s3_pg_backup
    es_snapshot = var.secret_data.s3_es_snapshot
  }
  s3_creds           = local.ceph_on ? try(module.ceph[0].credentials, {}) : { for k, v in local.s3_ext : k => v if v != null }
  barman_sidecar_tag = coalesce(try(var.operator_images["barman"].sidecar_tag, null), local.placeholder_version)
  pull_list          = local.pull ? [{ name = local.pull_name }] : []

  # Built-in values (YAML). Observability files are reused verbatim with the mirror host substituted.
  builtin_values = {
    cnpg = yamlencode({ image = local.img.cnpg, imagePullSecrets = local.pull_list })
    # barman chart renders {registry}/{repository}:{tag} (registry defaults to ghcr.io) for the plugin AND the sidecar CNPG injects into PG pods
    barman = yamlencode({
      image            = { registry = var.mirror_registry, repository = local.image_defaults.barman, tag = local.img.barman.tag }
      sidecarImage     = { registry = var.mirror_registry, repository = "${local.image_defaults.barman}-sidecar", tag = local.barman_sidecar_tag }
      imagePullSecrets = local.pull_list
    })
    cert_manager = yamlencode({
      crds            = { enabled = true }
      image           = { repository = "${local.img.cert_manager.repository}-controller", tag = local.img.cert_manager.tag }
      webhook         = { image = { repository = "${local.img.cert_manager.repository}-webhook", tag = local.img.cert_manager.tag } }
      cainjector      = { image = { repository = "${local.img.cert_manager.repository}-cainjector", tag = local.img.cert_manager.tag } }
      startupapicheck = { image = { repository = "${local.img.cert_manager.repository}-startupapicheck", tag = local.img.cert_manager.tag } }
      global          = { imagePullSecrets = local.pull_list }
    })
    eck = yamlencode({
      image            = local.img.eck
      config           = { containerRegistry = var.mirror_registry }
      imagePullSecrets = local.pull_list
    })
    # S3 only: no CSI drivers, no block/file pools, no toolbox/monitoring (smaller footprint, fewer images to mirror)
    rook_ceph = yamlencode({
      image                          = local.img.rook_ceph
      obcAllowAdditionalConfigFields = "maxObjects,maxSize,bucketLifecycle" # bucketPolicy stays off (can brick the store); lifecycle for the app bucket tmp/ rule
      imagePullSecrets               = local.pull_list
      csi                            = { rookUseCsiOperator = false, disableCsiDriver = "true", enableRbdDriver = false, enableCephfsDriver = false } # no ceph-csi-operator image to mirror
    })
    rook_ceph_cluster = yamlencode({
      operatorNamespace                 = var.namespaces.rook_ceph
      toolbox                           = { enabled = false }
      monitoring                        = { enabled = false }
      cephBlockPools                    = []
      cephFileSystems                   = []
      cephFileSystemVolumeSnapshotClass = { enabled = false }
      cephBlockPoolsVolumeSnapshotClass = { enabled = false }
      cephClusterSpec = {
        cephVersion = { image = "${local.ceph_image.repository}:${local.ceph_image.tag}" }
        mon         = { count = var.ceph.mon_count, allowMultiplePerNode = var.ceph.allow_multiple_mons_per_node }
        mgr         = { count = var.ceph.mgr_count, allowMultiplePerNode = var.ceph.allow_multiple_mons_per_node }
        dashboard   = { enabled = false }
        resources   = var.ceph.resources
        storage = merge(
          { useAllNodes = length(var.ceph.nodes) == 0, useAllDevices = false },
          var.ceph.device_filter == "" ? {} : { deviceFilter = var.ceph.device_filter },
          length(var.ceph.nodes) == 0 ? {} : { nodes = var.ceph.nodes },
        )
      }
      cephObjectStores = [{
        name = var.ceph.store_name
        spec = {
          metadataPool = { failureDomain = var.ceph.failure_domain, replicated = { size = var.ceph.metadata_replicas } }
          dataPool = merge(
            { failureDomain = var.ceph.failure_domain },
            var.ceph.data_pool.mode == "erasure" ?
            { erasureCoded = { dataChunks = var.ceph.data_pool.data_chunks, codingChunks = var.ceph.data_pool.coding_chunks } } :
            { replicated = { size = var.ceph.data_pool.replicas } },
          )
          preservePoolsOnDelete = true
          # uniform object, nulls dropped below: yamlencode must keep ints (a conditional of two object shapes becomes map(string))
          gateway = { for k, v in {
            instances         = var.ceph.rgw.instances
            resources         = var.ceph.rgw.resources
            port              = local.rgw_tls ? null : 80
            securePort        = local.rgw_tls ? 443 : null
            sslCertificateRef = local.rgw_tls ? local.rgw_tls_secret : null
          } : k => v if v != null }
        }
        storageClass = { enabled = true, name = local.ceph_bucket_class, reclaimPolicy = "Retain", parameters = { region = "us-east-1" } }
        ingress      = { enabled = false }
      }]
    })
    kube_prometheus_stack = replace(file("${local.obs_dir}/kube-prometheus-stack.values.yaml"), "MIRROR.example.com", var.mirror_registry)
    loki                  = replace(file("${local.obs_dir}/loki.values.yaml"), "MIRROR.example.com", var.mirror_registry)
    otel_collector        = replace(file("${local.obs_dir}/otel-collector.values.yaml"), "MIRROR.example.com", var.mirror_registry)
  }
  # Pull secrets for the observability charts (their image registry comes from the values files).
  pull_values = {
    kube_prometheus_stack = yamlencode({ global = { imagePullSecrets = local.pull_list } })
    loki                  = yamlencode({ global = { imagePullSecrets = local.pull_list } })
    otel_collector        = yamlencode({ imagePullSecrets = local.pull_list })
  }

  charts = {
    for k, d in local.chart_defaults : k => {
      enabled    = coalesce(try(var.charts[k].enabled, null), d.enabled)
      repository = coalesce(try(var.charts[k].repository, null), local.placeholder_repo)
      chart      = coalesce(try(var.charts[k].chart, null), d.chart)
      version    = coalesce(try(var.charts[k].version, null), local.placeholder_version)
      namespace  = d.ns
      values     = compact([local.builtin_values[k], local.pull ? try(local.pull_values[k], "") : "", try(var.charts[k].values, null)])
    }
  }

  enabled_charts = { for k, c in local.charts : k => c if c.enabled }
  # A chart whose repository/version is still a placeholder cannot be fetched: it is left out of the plan
  # (so `plan` works for review) and reported by the gate, which blocks apply.
  operator_charts = {
    for k, c in local.enabled_charts : k => c
    if length(regexall(local.marker, "${c.repository} ${c.version}")) == 0
  }
  inference_enabled = var.inference.enabled
  app_chart_remote  = var.app_chart.repository != ""

  marker = "PLACEHOLDER|MIRROR\\.example\\.com|CHANGEME|sha256:0{64}"

  # Anything below containing a placeholder marker blocks apply (see terraform_data.placeholder_gate).
  placeholder_probe = merge(
    { for k, c in local.enabled_charts : "chart.${k}.repository" => c.repository },
    { for k, c in local.enabled_charts : "chart.${k}.version" => c.version },
    { for k, i in local.img : "operator_images.${k}" => "${i.repository} ${i.tag}${k == "barman" ? " ${local.barman_sidecar_tag}" : ""}" if local.charts[k].enabled },
    { "var.mirror_registry" = var.mirror_registry, "images.registry" = var.images.registry,
    "images.digests" = "${var.images.backend_digest} ${var.images.web_digest}" },
    # loki's bundled values point at s3.example.com unless overridden
    try(local.enabled_charts["loki"].enabled, false) && !can(regex("endpoint", var.charts["loki"].values)) ? { "loki.s3.endpoint" = "MIRROR.example.com (s3.example.com in loki values)" } : {},
    local.ceph_on && !var.ceph.rgw.allow_plain_http && !local.rgw_tls ? { "ceph.rgw.tls_secret or ceph.rgw.cert_manager_issuer (TLS is required)" = "CHANGEME" } : {},
    local.ceph_on && var.ceph.rgw.tls_secret != "" && var.ceph.rgw.cert_manager_issuer == "" && var.s3.ca_secret == null ? { "s3.ca_secret (CA of ceph.rgw.tls_secret in the app namespace)" = "CHANGEME" } : {},
    local.ceph_on ? { "ceph.image" = "${local.ceph_image.repository} ${local.ceph_image.tag}" } : {},
    local.ceph_on && var.ceph.device_filter == "" && length(var.ceph.nodes) == 0 ? { "ceph.device_filter/ceph.nodes (explicit OSD devices, never all devices)" = "CHANGEME" } : {},
    local.app_chart_remote ? { "app_chart.repository" = var.app_chart.repository, "app_chart.version" = var.app_chart.version } : {},
    local.inference_enabled ? {
      "inference.llm"    = "${var.inference.llm.image} ${var.inference.llm.model_path}"
      "inference.rerank" = "${var.inference.rerank.image} ${var.inference.rerank.model_path}"
    } : {},
  )
  placeholders = sort([
    for k, v in local.placeholder_probe : k
    if length(regexall(local.marker, v)) > 0
  ])
}
