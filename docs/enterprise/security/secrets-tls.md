# Security: secrets, TLS and credentials

Part of the [security architecture](../security.md). Install steps are in the [deployment guide](../deployment-guide.md)
and the [Terraform guide](../deployment-terraform.md); rotation procedures are in the
[routine runbook](../runbook-routine.md#rotate-secrets).

## 5. Data protection in transit and at rest

- TLS in transit: ingress, plus service mesh or per-service TLS to Postgres, Elasticsearch and the S3 object store
  (operator choice; the chart does not force a mesh).
- Encryption at rest comes from the storage class and S3 object store SSE (KMS-backed where available). rOracle
  does not encrypt data itself.
- The inference endpoint receives only the minimum context (retrieved passages, the question); no
  documents leave the enclave. Which passages may be sent is governed by the [labels](access-control.md#13-sensitivity-labels).
- Deletion and retention: [data retention](data-retention.md).

## Secrets handling

- Credentials are only referenced by Secret name (`existingSecret`, operator Secrets); the chart renders no secret values.
- No secret on any command line: the ES hook reads curl config from stdin; no `set -x`. The Terraform bucket-setup Job reads the admin keys from Rook's Secret through `secretKeyRef`, never from arguments.
- The app Postgres password is in two Secrets (CNPG basic-auth and `ROR_DATABASE_URL`) because CNPG's generated URI lacks the `+psycopg` driver: rotate both.
- `ROR_ELASTIC_URL` carries the `roracle_app` credentials as userinfo, so it lives in the app Secret.
  Credentials can be supplied as `ROR_ELASTIC_USERNAME`/`ROR_ELASTIC_PASSWORD` and `ROR_DATABASE_PASSWORD` secret keys instead of URL userinfo.

### Terraform secrets module

`deploy/terraform` has two modes (`secrets_mode`, default `create`):

| Mode | Behaviour |
|---|---|
| `create` | `modules/secrets` builds every Secret the chart references from the sensitive `secret_data` variable: the app Secret (`ROR_DATABASE_URL`, `ROR_ELASTIC_URL`, S3 keys when they live in the app Secret, `ROR_LLM_API_KEY`, `secret_data.extra`), the CNPG basic-auth Secret, the ES app user Secret, and the PG backup and ES snapshot S3 Secrets. **Values land in Terraform state** and in the cluster: protect the state backend |
| `existing` | Nothing is created; the Secrets named in `secret_names` must exist (External Secrets, Vault or `kubectl`). Preferred where a secret manager exists |

Preconditions enforced at plan time (module `secrets`):

- `secret_data.llm_api_key` is required while `models.llm_key_used` is true.
- With external S3, the Postgres backup and ES snapshot identities must be set **and differ from the app's access key**, so the app identity cannot write or delete backups (unless backups are switched off in `backups_enabled`).
- S3 credentials must be present (external) or `object_store=rook-ceph` used.

The `secret_data` variable documents a password charset of `[A-Za-z0-9._~-]` (for example `openssl rand -hex 32`); it is not enforced by a precondition, and the generated URLs are `urlencode`d.

With `object_store=rook-ceph`, the S3 keys of the app live in their own Secret (`secret_names.s3`) created by the
Ceph module in every mode, and the PG backup and ES snapshot identities are created automatically.

### Least privilege

- The app's ES user `roracle_app` can only act on `chunks*` (create_index, manage, read, write, view_index_metadata) plus cluster `monitor`; the snapshot hook alone uses `elastic`.
- Object-store identities are separate per bucket. With Terraform rook-ceph each bucket has its own Ceph user (app bucket: OBC user; PG backup and ES snapshot: dedicated writers with a bucket policy that denies version deletion, lock bypass and policy/lifecycle changes); the backup buckets are created with S3 object lock and a default retention (GOVERNANCE, 30 d), and the app identity has no grant on them.
- The Ceph admin user `roracle-admin` (bucket owner, creates the buckets) is the break-glass identity: as owner it can still bypass GOVERNANCE retention, so guard its Secret `rook-ceph-object-user-<store>-roracle-admin` (RBAC) and prefer COMPLIANCE mode only after you accept that nobody can shorten it.
- On external S3 you must do the same: Terraform requires separate backup identities; bucket creation with object lock, retention and policy are operator duties (see the Terraform guide, "External S3 commands").
- Backups should be replicated to a separate site; a single-cluster object store (including the backup buckets on the same Ceph cluster) does not protect against loss of the cluster ([backup and DR](../backup-dr.md)).

### Personal access token hashing

A PAT is `ror_pat_` plus 32 random bytes (`secrets.token_urlsafe(32)`). Only its SHA-256 hex digest is stored
(`personal_access_tokens.token_hash`), together with the first 12 characters as a display prefix. The digest is
**unsalted and has no server-side pepper**: with 256 bits of random input a salt adds nothing, and there is no
secret to rotate, so no rotation step invalidates all tokens at once. To cut off one user use `DELETE /api/admin/users/{id}/pats`;
to cut off everyone set `ROR_PATS_ENABLED=false`. The plaintext is shown once at creation and is not stored.

## TLS

### Trust inside the cluster (interim)

ES issues its TLS certificate from a private CA and the S3 endpoint may use one too (Terraform requires TLS on Rook-Ceph RGW:
cert-manager issued or supplied). An init container concatenates certifi's
bundle with the ECK CA and `caBundle.extraSecrets` (the S3 CA) into an emptyDir mounted over certifi's `cacert.pem`
(`caBundle.certifiPath`, coupled to the image's Python layout) and sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` (httpx/openai) plus `ROR_ELASTIC_CA_CERTS` and `ROR_S3_CA_BUNDLE` (ES client, boto3) to that file.
Verification is always on; there is no `verify=False` path. Details: [deployment guide](../deployment-guide.md).

### Certificate lifetimes and rotation

| Item | Lifetime | What to do |
|---|---|---|
| Rook-Ceph RGW self-signed CA (Terraform, cert-manager) | 10 years, renewed 30 days before | The CA copy in the app namespace is a Terraform snapshot: after a CA renewal re-run `terraform apply`, then restart api and worker |
| RGW leaf certificate | 1 year, renewed 30 days before | Rook may not reload a changed Secret: after a renewal run `kubectl -n rook-ceph rollout restart deploy -l app=rook-ceph-rgw` |
| ECK CA | operator-managed | After an ECK CA rotation restart api and worker (`kubectl rollout restart deploy/roracle-api deploy/roracle-worker`) |
| Keycloak signing keys | per realm policy | Rotate in Keycloak; rOracle reloads the JWKS on an unknown `kid` (at most once per 60 s) |
| Ingress / org proxy certificates | operator-owned | Outside the chart. HSTS is not sent by the web pod: set it at the ingress or proxy |

A third-party ClusterIssuer whose Secret has no `ca.crt` is refused by the Terraform module (use `tls_secret` and `s3.ca_secret`).
Full procedure: [Terraform guide, TLS rotation](../deployment-terraform.md).

### ClamAV fail-closed configuration

`clamd.conf` comes from a read-only ConfigMap: `StreamMaxLength`, `MaxFileSize`, `MaxScanSize` and `PCREMaxFileSize` = `parsers.clamav.limits` (1100M, render fails
if below `ROR_MAX_UPLOAD_BYTES`/`ROR_EVIDENCE_MAX_BYTES`), `AlertExceedsMax yes`, `MaxThreads 4` (`/tmp` must be >= limits x threads, guarded).
Readiness and startup probes check the effective limits with `clamconf` plus a clamd PING, so a pod with fail-open config never becomes ready.
An over-limit stream ends in a scanner error and the backend treats it as non-retryable (no partial scan). Signatures: `parsers.clamav.mirrorUrl`;
without a mirror only baked-in signatures exist.
