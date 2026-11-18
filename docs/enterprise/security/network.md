# Security: network and platform hardening

Part of the [security architecture](../security.md). Source: `deploy/helm/roracle/templates/networkpolicy.yaml` and
`networkpolicy-data.yaml`, the Terraform modules `ceph` and `inference`, and `frontend/nginx/`. Chart keys are in
[Helm values](../../reference/helm-values.md).

## 7. Platform hardening

What the chart sets (values `podSecurityContext` and `containerSecurityContext`, parser and data-tier templates):
`runAsNonRoot`, seccomp `RuntimeDefault`, `allowPrivilegeEscalation: false`, all capabilities dropped, read-only root
filesystem (exceptions below), no service-account token automount on the service account, the migrate job, the ES
snapshot job, the ES pods and the parser pods, and the NetworkPolicies in this page. Dependencies come only from internal mirrors
(see [air-gap mirror manifest](../airgap-mirror-manifest.md)); lock files are committed (`uv.lock`, `pnpm-lock.yaml`).

What the repository does **not** do, so a security review should not assume it: `.gitlab-ci.yml` has no SBOM
(CycloneDX), vulnerability-scan or image-signing (cosign) stage, the chart does not label the namespace for Pod
Security Admission (the pod specs follow the controls the `restricted` profile asks for, but enforcing the profile
is the operator's), and no STIG baseline is applied. Earlier drafts of this page claimed signed images, SBOMs and
STIG alignment; none of that is in the repository. See [known limits](known-limits.md).

## 11. NetworkPolicies

`<fn>` is the chart fullname (for example `roracle`). The application-tier policies are rendered when `networkPolicy.enabled`
is true (the default); the data-tier ones whenever the Postgres or Elasticsearch mode is bundled. Policies are additive: the first one denies everything and the rest open single paths.
They need a CNI that enforces NetworkPolicy. Policies match **after DNAT**: use pod, node or load-balancer CIDRs,
never Service ClusterIPs. An empty CIDR list in `networkPolicy.*` produces no rule, so that traffic stays denied.

### Application tier (`networkpolicy.yaml`)

| Policy | Applies to | Allows |
|---|---|---|
| `<fn>-default-deny` | every pod in the namespace | Nothing: `Ingress` and `Egress` both denied |
| `<fn>-allow-dns` | all pods except gotenberg, draftgotenberg, docling, grobid (and clamav unless `parsers.clamav.mirrorUrl` is set) | Egress to DNS (`networkPolicy.dns`, default `kube-system` pods `k8s-app=kube-dns`) on 53/UDP and 53/TCP |
| `<fn>-ingress-to-web` | web | Ingress on 8080 from the ingress controller namespace (`networkPolicy.ingressNamespaceSelector`, default `kubernetes.io/metadata.name: ingress-nginx`). Rendered only when `ingress.enabled` |
| `<fn>-ingress-to-api` | api | Ingress on 8000 from the same namespace. The chart's ingress routes only `/mcp` and `/.well-known/oauth-protected-resource` to the api; everything else goes to web. Rendered only when `ingress.enabled` |
| `<fn>-api-from-web` | api | Ingress on 8000 from web pods |
| `<fn>-web-to-api` | web | Egress to api on 8000 |
| `<fn>-app-egress-data` | api, worker, migrate | Egress to Postgres (bundled CNPG pods on 5432, plus the pooler pods when enabled; otherwise `networkPolicy.externalCIDRs.postgres`), Elasticsearch (bundled ECK pods on 9200, otherwise `externalCIDRs.elasticsearch`) and object storage (`networkPolicy.s3`: CIDRs and/or an in-cluster peer, ports 443 and 9000 by default). Rendered when at least one rule is non-empty |
| `<fn>-es-snapshot-egress` | the ES snapshot job pods | Egress to the ES pods (only with `elasticsearch.mode=eck`) |
| `<fn>-app-egress-external` | api, worker | Egress to `egressCIDRs.llm` (443), `egressCIDRs.oidc` (443, JWKS) and `egressCIDRs.otel` (4318); each is a CIDR list and/or an in-cluster peer. Empty = denied |
| `<fn>-worker-egress` | worker | Egress to `connectors.egressCIDRs` on `connectors.ports` (default 443; no CIDRs by default), to `egressCIDRs.parsers` (external parsers), and to each enabled in-chart parser on its port (gotenberg 3000, docling 5001, grobid 8070, clamav 3310) |
| `<fn>-api-egress-draft` | api | Egress to the `draftgotenberg` pods only (draft PDF builds) |
| `<fn>-parser-gotenberg`, `-parser-draftgotenberg`, `-parser-docling`, `-parser-grobid`, `-parser-clamav` | each enabled parser | Ingress on its port from the worker (draftgotenberg: from the api). `Egress` is listed with **no rules**, so parsers have no egress. Only ClamAV gets egress, to `parsers.clamav.mirrorCIDRs` on 443 and 80, and only when `parsers.clamav.mirrorUrl` is set (then `mirrorCIDRs` is required) |
| `<fn>-allow-scrape` | api, worker | Ingress from the `networkPolicy.monitoringNamespace` namespace (default `monitoring`) on `networkPolicy.scrape.ports` (8000, 8001), when `networkPolicy.scrape.enabled` (default true). This is how `/metrics` stays cluster-internal; the repo ships no api/worker PodMonitor ([observability](../observability.md)) |

### Data tier (`networkpolicy-data.yaml`)

Rendered whenever the mode is bundled (not behind `networkPolicy.enabled`); each applies to its own pods.

| Policy | Applies to | Allows |
|---|---|---|
| `<fn>-data-es` (`elasticsearch.mode=eck`) | ES pods | Ingress 9200 from app pods (api, worker, migrate, es-snapshot) and the ECK operator namespace (`networkPolicy.operatorNamespaces.eck`, default `elastic-system`); 9300 and 9200 between ES pods. Egress to ES pods, object storage (`networkPolicy.s3`) and DNS |
| `<fn>-data-pg` (`postgres.mode=cnpg`) | Postgres pods | Ingress 5432 from app pods, peer Postgres pods and the pooler; 8000 from the CNPG operator namespace (`operatorNamespaces.cnpg`, default `cnpg-system`); 9187 from `networkPolicy.monitoringNamespace`. Egress to Postgres peers, object storage (backups), the kube-apiserver (`networkPolicy.kubeAPI`) and DNS |
| `<fn>-data-pg-pooler` (cnpg with `postgres.pooler.enabled`) | pooler pods | Ingress 5432 from app pods, 8000 from the CNPG operator, 9127 from monitoring. Egress to Postgres, the kube-apiserver and DNS |

Object storage is always external to the chart: egress for api/worker/migrate and the CNPG/ES data tier goes only to
`networkPolicy.s3` (CIDRs and/or an in-cluster peer, for example the Rook-Ceph RGW pods). Other egress is limited to peers,
DNS and (instance managers) the kube-apiserver. **In production set `networkPolicy.kubeAPI.cidrs`** to the apiserver
endpoint IPs: with the default empty list, ports 443 and 6443 are allowed to any destination for the instance managers.

### Terraform-created policies

| Policy | Namespace | Allows |
|---|---|---|
| `roracle-rgw-ingress` (module `ceph`, `object_store=rook-ceph`) | the Ceph namespace, RGW pods | Ingress on the RGW container port (443 with TLS, else 80) from the app namespace and from pods in the Ceph namespace only |
| `default-deny`, `allow-roracle-ingress`, `allow-dns-egress` (module `inference`) | the in-cluster inference namespace | Deny all; ingress only from the rOracle api and worker pods of the release on the inference port; egress only to DNS in `kube-system`. The root module opens matching app-namespace egress to that namespace on port 8000 |

## 12. Web tier and pod baseline

### Headers sent by the web pod

The web pod's nginx includes `frontend/nginx/security-headers.conf` at server level and in every location that sets
headers. Exception: `/docs/` (the bundled documentation site, same origin) includes `docs-security-headers.conf` instead, whose CSP allows `script-src 'self' 'unsafe-inline'` for the site's inline bootstrap scripts and has no OIDC origins; docs must never contain user-supplied HTML. `ROR_OIDC_ORIGIN` is derived from `ROR_OIDC_AUTHORITY` at pod start.

Upload size: nginx `client_max_body_size` is 1100m (>= `ROR_EVIDENCE_MAX_BYTES`, 1 GiB); any proxy or ingress in front must be at least that large.

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; worker-src 'self' blob:; connect-src 'self' ${ROR_OIDC_ORIGIN}; frame-src 'self' ${ROR_OIDC_ORIGIN}; frame-ancestors 'self'; base-uri 'self'; form-action 'self' ${ROR_OIDC_ORIGIN}; object-src 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: SAMEORIGIN
Permissions-Policy: camera=(), microphone=(), geolocation=()
Cross-Origin-Opener-Policy: same-origin
```

- `frame-ancestors 'self'` / `SAMEORIGIN` / `frame-src` OIDC origin exist for the oidc-client-ts silent-renew iframe (`/auth/silent`).
  The Keycloak realm CSP `frame-ancestors` must allow the app origin.
- `style-src 'unsafe-inline'` is required by the UI toolkit's inline styles; scripts stay `'self'` only (except under `/docs/`, above).
- HSTS is not sent by the web pod: set `Strict-Transport-Security` at the ingress or org proxy.
- Runtime OIDC config is substituted into the JS bundle by nginx; substituted JS is cached 1 h (not immutable).
- The web proxy answers 404 for `/api/metrics` and `/api/readyz`; the API's own `/healthz`, `/readyz` and `/metrics`
  are for the kubelet and Prometheus inside the cluster.

### Pod security baseline and exceptions

All pods: `runAsNonRoot`, seccomp `RuntimeDefault`, `allowPrivilegeEscalation: false`, all capabilities dropped, read-only root filesystem
with emptyDir for writable paths; api/worker/migrate run as UID 10001; migrate does not mount a service-account token.

| Exception | Why |
|---|---|
| Elasticsearch pods (and Ceph daemons): root filesystem not read-only | operator-managed pods; their data paths are PVCs |
| ES `node.store.allow_mmap: false` | avoids ECK's privileged `vm.max_map_count` sysctl init container (slower; drop when nodes are tuned) |
| ClamAV: signature DB seeded into an emptyDir; `/init-unprivileged` entrypoint | non-root + read-only FS; the default `/init` needs root |
| GROBID: `-XX:-UseContainerSupport -Xmx4608m` | JDK crash on cgroup v2; heap must be explicit |
