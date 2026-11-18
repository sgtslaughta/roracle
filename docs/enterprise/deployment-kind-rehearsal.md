# Kind rehearsal and Ceph decommissioning

Audience: operators and SRE. Before a first real install, rehearse the [Terraform stack](deployment-terraform.md) on a throwaway [kind](https://kind.sigs.k8s.io/) cluster, with a local registry standing in for the air-gap mirror. This page also holds the steps to decommission a Rook-Ceph object store. Back to the [enterprise index](README.md); terms are in the [glossary](../glossary.md).

## 1. What the script does

`deploy/terraform/test/kind-e2e.sh` applies the Terraform stack for real on a kind cluster. A local `registry:2` is the mirror (`localhost:5055`, with containerd mirror hosts on the node). The script is idempotent, every step skips what already exists, and it never touches `~/.kube/config`.

| Subcommand | Does |
|---|---|
| `up` | pinned tools, the registry, the kind cluster, mirrors the images and operator charts (OCI) into the registry, builds and pushes the rOracle backend and web images |
| `apply` | gate checks (separate state), `terraform init`, `plan` and `apply` of `environments/kind.tfvars` |
| `verify` | pods Ready, migrate job succeeded, api `/healthz` and `/readyz` (db, Elasticsearch, S3), web served, every image outside `kube-system` comes from the mirror, `protect_data = true` blocks destroy |
| `destroy` | flips `protect_data` with `terraform state mv`, then `terraform destroy` |
| `down` | deletes the cluster and the registry (the tools cache stays) |
| `all` | `up`, `apply`, `verify`, `destroy`, `down`; a trap runs `down` unless `KIND_E2E_KEEP=1` |

```sh
deploy/terraform/test/kind-e2e.sh all      # up, apply, verify, destroy, down
```

State, kubeconfig and tools live in the git-ignored `deploy/terraform/test/.tools/`. Pinned tools: kind v0.33.0, kubectl v1.34.12, Helm 3.17.3, `registry:2.8.3` by digest; the script checks their sha256 and the pinned tools shadow any on `PATH`. Names: cluster `rorac-kind`, registry `rorac-kind-registry`. Requirements: bash 4+, docker, terraform, curl, jq, tar, openssl, sha256sum, and internet access on the first run (the rehearsal itself fetches upstream images once, to mirror them). Those downloads are why this is a connected-workstation exercise, not an air-gapped one.

Reference timings on a 16 CPU host with a warm Docker cache: up 41 s, apply 80 s (18 resources, migrate hook included), verify 3 s, destroy 140 s, down 5 s; a whole cycle takes about 5 minutes (cold: add upstream pulls and image builds).

## 2. What it proves and what it does not

Proven by `verify`: operator charts and images come from the mirror; the placeholder gate refuses `apply` with `MIRROR.example.com` and creates nothing, while `allow_placeholders=true` passes it; the release comes up; `terraform destroy` with `protect_data=true` is refused by `prevent_destroy` with the data tier intact; after `state mv` to the unprotected variant, destroy with `protect_data=false` removes everything.

Known gaps:

- **Object storage is external.** Kind has no raw disks, so the default profile uses a MinIO stand-in (`test/s3-stub.yaml`, a dev and CI image never shipped) with the pod CIDR as `network.s3_cidrs`. Postgres and ES backups and snapshots are off, so the barman plugin and cert-manager are installed but unused.
- Docling, GROBID, ClamAV and the draft Gotenberg are off (memory); only the ingest Gotenberg runs. There is no ingress controller, no Keycloak and no LLM, so sign-in and chat are not exercised.
- The node still has internet. Image origin is proven by the pod specs (all `localhost:5055/...`), not by network isolation.
- Destroy leaves operator CRDs behind (Helm never deletes CRDs of installed charts; Terraform prints a warning per CRD). Delete them by hand on a reused cluster; `down` removes the whole cluster.
- kindnet NetworkPolicy enforcement is basic, and `network.kube_api_cidrs` is left empty (any destination on 443 and 6443).

Profile files: `environments/kind.tfvars` and `environments/kind-values.yaml` (1 replica, small requests, ES 1.5 GiB limit, observability and inference off).

CI has a manual job `iac-kind-e2e` (stage `terraform`, `when: manual`). It needs a privileged runner with Docker-in-Docker, the dind service sharing the job's network namespace, about 8 GB RAM, internet on first run and `KIND_E2E_IMAGE`; the job fails fast when that is unset. It is not part of the default pipeline.

## 3. Live Rook-Ceph rehearsal (local only)

```sh
deploy/terraform/test/kind-e2e.sh ceph      # = KIND_E2E_PROFILE=ceph ... all;  KIND_E2E_KEEP=1 keeps the cluster on failure
```

`kind-e2e-ceph.sh` is sourced by `kind-e2e.sh` for this profile. It needs a privileged kind node that can create loop devices, about 16 GB RAM and about 8 GB of disk (sparse files). Do not run it on shared or Docker-in-Docker runners: loop devices are host-global. Three 8 GiB sparse files are attached as loop devices inside the kind node; each device and file is recorded in `test/.tools/loops.list`, and `down` (also the exit trap of `all`) detaches and deletes exactly those.

Profile: `environments/kind-ceph.tfvars` plus the generated `test/.tools/ceph-osd.tfvars` (1 mon, 1 mgr, 1 RGW, failure domain `osd`, erasure 2+1 across the three OSDs, cert-manager self-signed TLS, backups on, `allowLoopDevices` and a single-host CRUSH override as test-only chart values). Reference timings: up 70 s (loop devices and about 1.5 GB of Ceph images), apply 305 s, verify 55 s, destroy bounded to 15 minutes, whole run about 745 s.

Verified end to end: CephCluster, CephObjectStore and users Ready; the bucket-setup Job completed; app S3 put, get and delete over HTTPS with the copied CA; the `tmp-expire` lifecycle on the app bucket; object lock (GOVERNANCE, 30 days) on both backup buckets; the app key gets `AccessDenied` on the backup buckets; the writers get `AccessDenied` on deleting a locked version and on lock bypass but may write and create delete markers; a CNPG backup through the barman plugin completes; the ES snapshot repository verifies and passes the analysis API, and an SLM execute plus a snapshot delete work.

Known gaps: `terraform destroy` does not finish (Rook blocks deleting the object store while buckets exist; the script bounds it and removes the cluster). One host and loop devices prove the logic, not durability or performance, so a multi-node Ceph install is still a first-install rehearsal.

## 4. Decommissioning the object store (rook-ceph)

Locked backup buckets and Rook's delete protections make this deliberate. Do it in this order. The admin identity is the break-glass identity.

1. **Export and verify.** Copy every bucket to the new store (`rclone sync` or `aws s3 sync` with `--endpoint-url`, one run per bucket: `roracle`, `roracle-pg-backup`, `roracle-es-snapshots`) and compare object counts and sizes. Take a last Postgres backup and ES snapshot first ([runbook](operations-runbook.md)). Point the app at the new store before deleting anything.
2. **Admin credentials**, read from the cluster and never from state: `kubectl -n rook-ceph get secret rook-ceph-object-user-roracle-roracle-admin -o jsonpath='{.data.AccessKey}' | base64 -d` (and `.data.SecretKey`). Export `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_CA_BUNDLE` (the `ca.crt` of `roracle-rgw-tls`) and `S3=https://rook-ceph-rgw-roracle.rook-ceph.svc:443`. Run from a pod in the Ceph or app namespace (for example the backend image) or through `kubectl port-forward`.
3. **Empty the buckets.** App bucket (owned by its claim user; use the `roracle-s3` keys, because deleting the claim in step 4 does not empty it): `aws --endpoint-url $S3 s3 rm s3://roracle --recursive`. Backup buckets (GOVERNANCE): the admin owns them and may bypass retention. List every version with `aws --endpoint-url $S3 s3api list-object-versions --bucket roracle-pg-backup --query '[Versions,DeleteMarkers][][].{Key:Key,VersionId:VersionId}' --output json > v.json`, then for each entry run `aws --endpoint-url $S3 s3api delete-object --bucket roracle-pg-backup --key <Key> --version-id <VersionId> --bypass-governance-retention` (loop with `jq -c '.[]'`, repeat until the listing is empty), and do the same for `roracle-es-snapshots`. Then `aws --endpoint-url $S3 s3api delete-bucket --bucket <name>`.
   **COMPLIANCE mode cannot be shortened or bypassed by anyone**, not even the admin: the data stays until each version's retention expires (`aws s3api head-object --version-id` shows the retain-until date). Wait it out; the buckets, and therefore the object store, cannot be deleted before.
4. **Remove identities and claims.** `kubectl -n roracle delete objectbucketclaim --all`, then `kubectl -n rook-ceph delete cephobjectstoreuser --all`. Rook refuses while buckets still exist; a stuck delete means step 3 is incomplete (`kubectl -n rook-ceph describe` shows the reason).
5. **Destroy.** Move the release to the unprotected address with `terraform state mv` (see [Terraform, protecting data](deployment-terraform.md)), then `terraform destroy -var-file=... -var protect_data=false`. Rook deletes the `CephObjectStore` only when no buckets or users remain; if the destroy times out, fix steps 3 and 4 and run it again. Pools are kept (`preservePoolsOnDelete`) and the StorageClass reclaim policy is `Retain`.
6. **Wipe the OSD disks before any reinstall.** Rook leaves Ceph metadata on the devices and `/var/lib/rook` on every node. On each node run `rm -rf /var/lib/rook`; for each OSD device run `sgdisk --zap-all /dev/<dev>` and `dd if=/dev/zero of=/dev/<dev> bs=1M count=100 oflag=direct,dsync` (or `wipefs -a`); and if LVM volumes were created, `ls /dev/mapper/ceph-* | xargs -I% dmsetup remove %` and `rm -rf /dev/ceph-*`. A reinstall on unwiped disks fails OSD prepare because the cluster id differs. Consider the Rook `cleanupPolicy` (set before deleting the CephCluster) for automated wiping.
