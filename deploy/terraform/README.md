# rOracle Terraform stack (module reference)

Terraform wraps the Helm chart `deploy/helm/roracle` ([ADR-0016](../../docs/adr/0016-terraform-iac.md)). It targets a pre-existing cluster through a kubeconfig and manages no cloud resources. Providers: `hashicorp/kubernetes ~> 3.0`, `hashicorp/helm ~> 3.0`; Terraform `>= 1.6`.

Operator documentation lives in the docs site sources:

- `docs/enterprise/deployment-terraform.md`: prerequisites, placeholders and the gate, secrets modes, plan and apply order, `protect_data`, Rook-Ceph and external S3, state backends.
- `docs/enterprise/deployment-kind-rehearsal.md`: the local kind rehearsal and decommissioning the Ceph object store.
- `docs/enterprise/deployment-guide.md` and `docs/enterprise/upgrades.md`: the chart and upgrade procedure.

## Layout

| Path | What |
|---|---|
| `main.tf`, `locals.tf`, `app-values.tf` | placeholder gate, module wiring, typed variables rendered to Helm values |
| `variables.tf`, `variables-app.tf`, `variables-ceph.tf` | root variables (cluster access, mirror, operators, app, secrets, inference, object store) |
| `modules/platform` | app namespace, optional ResourceQuota and LimitRange, image pull Secret |
| `modules/operators` | operator `helm_release`s (cnpg, barman, cert-manager, eck, rook-ceph, observability charts) and CRD readiness |
| `modules/ceph` | Rook-Ceph buckets, RGW TLS, locked backup buckets (`bucket-setup.py`) |
| `modules/secrets` | every Secret the chart references |
| `modules/inference` | optional GPU LLM and reranker (off by default) |
| `modules/app` | the `roracle` `helm_release` (`protected` and `unprotected` variants for `protect_data`) |
| `environments/` | `example.tfvars`, `prod.example.tfvars`, `kind.tfvars`, `kind-ceph.tfvars`, `kind-values.yaml` |
| `test/` | `kind-e2e.sh`, `kind-e2e-ceph.sh`, `ceph-checks.py`, `s3-stub.yaml` |
| `backend.tf` | commented state backend examples |

## Quick start

```sh
cd deploy/terraform
terraform init
terraform plan  -var-file=environments/dev.tfvars -var allow_placeholders=true   # review (copy environments/example.tfvars first)
terraform apply -var-file=environments/dev.tfvars                                 # placeholders must be replaced
```

## Verification

`terraform fmt -check -recursive`, `terraform init -backend=false`, `terraform validate` (CI job `terraform`). The kind rehearsal: `test/kind-e2e.sh all` (manual CI job `iac-kind-e2e`).
