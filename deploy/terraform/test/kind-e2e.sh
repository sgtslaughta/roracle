#!/usr/bin/env bash
# Apply rehearsal of deploy/terraform on a local kind cluster with a local registry as the "air-gap mirror".
#   kind-e2e.sh up|apply|verify|destroy|down|all     (all = up, apply, verify, destroy, down)
# up      tools (kind/kubectl/helm3), registry:2 (rorac-kind-registry), kind cluster using it, mirror images + operator
#         charts (OCI) into it, build + push the rOracle images
# apply   gate checks (separate state), terraform init/plan/apply of environments/kind.tfvars
# verify  pods Ready, migrate job, API /healthz, web, images only from the mirror, protect_data blocks destroy
# destroy protect_data flip (state mv) + terraform destroy      down  delete cluster + registry (+ tools untouched)
# Needs bash 4+, docker, terraform, curl, jq, tar, openssl, sha256sum. KIND_E2E_KEEP=1: `all` keeps the cluster on failure. Idempotent: every step skips what already exists. Never touches ~/.kube/config.
set -euo pipefail
# profile: default = external S3 stand-in; `ceph` = live Rook-Ceph on loop devices (local only, see kind-e2e-ceph.sh and the README)
PROFILE="${KIND_E2E_PROFILE:-s3stub}"
if [ "${1:-}" = ceph ]; then PROFILE=ceph; set -- all; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$TF_DIR/../.." && pwd)"
TOOLS="${KIND_E2E_TOOLS:-$HERE/.tools}"
CLUSTER=rorac-kind
REG_NAME=rorac-kind-registry
REG_PORT=5055 # must match environments/kind.tfvars
REG="localhost:$REG_PORT"
REGISTRY_IMAGE=registry:2.8.3@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373
export KUBECONFIG="$TOOLS/kubeconfig"
export TF_DATA_DIR="$TOOLS/tfdata"
export HELM_PLUGINS="$TOOLS/helm/plugins" HELM_CONFIG_HOME="$TOOLS/helm/config" HELM_CACHE_HOME="$TOOLS/helm/cache" HELM_DATA_HOME="$TOOLS/helm/data" HELM_REGISTRY_CONFIG="$TOOLS/helm/registry.json"
STATE="$TOOLS/kind.tfstate"
NS=roracle
REL=roracle
VERSION="$(cat "$REPO/VERSION")"
TFVARS=environments/kind.tfvars
[ "$PROFILE" = ceph ] && TFVARS=environments/kind-ceph.tfvars
export PATH="$TOOLS/bin:$PATH"

# Pinned tools (kind, kubectl, helm 3), sha256 verified on download (linux); the tools dir shadows anything on PATH.
KIND_VERSION=v0.33.0
KUBECTL_VERSION=v1.34.12
HELM_VERSION=v3.17.3
declare -A SHA=(
  [kind-amd64]=aee6151561422756b764a4ae28e7f44cda5af5a9eead3cc9985112b1de8d8e0d
  [kind-arm64]=20022bee6cfcd5086cb7234d218e3454e6090022f2a8f55d1fa7fcf42c3867a2
  [kubectl-amd64]=90b7b9058ffeb5c10710bb1c73f541eaf426fb1b4e87df435db669de9a2564a2
  [kubectl-arm64]=c819e3eb6a540d4f1e6aacaac85580bf0793bd1e7d05095a47407733ccf0cfb7
  [helm-amd64]=ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd
  [helm-arm64]=7944e3defd386c76fd92d9e6fec5c2d65a323f6fadc19bfb5e704e3eee10348e
)
ARCH="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"

# Operator charts (name, repo url, version). Same pins as environments/kind.tfvars.
CHARTS=(
  "cloudnative-pg https://cloudnative-pg.github.io/charts 0.26.0"
  "plugin-barman-cloud https://cloudnative-pg.github.io/charts 0.9.0"
  "cert-manager https://charts.jetstack.io v1.17.0"
  "eck-operator https://helm.elastic.co 2.16.0"
)
# Images: "<upstream ref> <path in mirror>". Object storage is external here (Rook-Ceph needs raw disks, kind has none). Terraform values render <mirror>/<path>; chart images keep the upstream
# path under global.imageRegistry. Keep in sync with the tag pins in kind.tfvars and chart values.
IMAGES=(
  "ghcr.io/cloudnative-pg/cloudnative-pg:1.26.0 cloudnative-pg/cloudnative-pg:1.26.0"
  "ghcr.io/cloudnative-pg/plugin-barman-cloud:v0.16.0 cloudnative-pg/plugin-barman-cloud:v0.16.0"
  "ghcr.io/cloudnative-pg/plugin-barman-cloud-sidecar:v0.16.0 cloudnative-pg/plugin-barman-cloud-sidecar:v0.16.0"
  "quay.io/jetstack/cert-manager-controller:v1.17.0 jetstack/cert-manager-controller:v1.17.0"
  "quay.io/jetstack/cert-manager-webhook:v1.17.0 jetstack/cert-manager-webhook:v1.17.0"
  "quay.io/jetstack/cert-manager-cainjector:v1.17.0 jetstack/cert-manager-cainjector:v1.17.0"
  "quay.io/jetstack/cert-manager-startupapicheck:v1.17.0 jetstack/cert-manager-startupapicheck:v1.17.0"
  "docker.elastic.co/eck/eck-operator:2.16.0 eck/eck-operator:2.16.0"
  "ghcr.io/cloudnative-pg/postgresql:16.6 ghcr.io/cloudnative-pg/postgresql:16.6"
  "docker.elastic.co/elasticsearch/elasticsearch:8.15.3 docker.elastic.co/elasticsearch/elasticsearch:8.15.3"
  "curlimages/curl:8.10.1 curlimages/curl:8.10.1"
  "gotenberg/gotenberg:8.20.1 gotenberg/gotenberg:8.20.1"
)
[ "$PROFILE" = ceph ] || IMAGES+=(
  "bitnamilegacy/minio:2025.5.24 bitnamilegacy/minio:2025.5.24" # S3 stand-in (test/s3-stub.yaml)
)
if [ "$PROFILE" = ceph ]; then
  IMAGES+=("docker.io/rook/ceph:v1.18.0 rook/ceph:v1.18.0" "quay.io/ceph/ceph:v19.2.3 ceph/ceph:v19.2.3")
  CHARTS+=("rook-ceph https://charts.rook.io/release v1.18.0" "rook-ceph-cluster https://charts.rook.io/release v1.18.0")
fi

log() { printf '\n== %s (%ss)\n' "$*" "$SECONDS" >&2; }
die() { echo "FAIL: $*" >&2; exit 1; }
tf() { [ -s "$TOOLS/secrets.json" ] || die "missing $TOOLS/secrets.json (run apply first)"; (cd "$TF_DIR" && TF_VAR_secret_data="$(cat "$TOOLS/secrets.json")" terraform "$@"); }
tfargs=(-input=false -var-file="$TFVARS")
[ "$PROFILE" = ceph ] && tfargs+=(-var-file="$TOOLS/ceph-osd.tfvars")
# shellcheck disable=SC1091
[ "$PROFILE" = ceph ] && source "$HERE/kind-e2e-ceph.sh"

fetch() { # name url dest sha
  local f="$TOOLS/dl/$1"; mkdir -p "$TOOLS/dl" "$TOOLS/bin"
  [ -f "$f" ] || curl -fsSL "$2" -o "$f"
  echo "$4  $f" | sha256sum -c --status || { rm -f "$f"; die "sha256 mismatch for $1"; }
}
tools() {
  mkdir -p "$TOOLS/bin"
  if [ ! -x "$TOOLS/bin/kind" ]; then
    fetch kind "https://kind.sigs.k8s.io/dl/$KIND_VERSION/kind-linux-$ARCH" x "${SHA[kind-$ARCH]}"
    install -m755 "$TOOLS/dl/kind" "$TOOLS/bin/kind"
  fi
  if [ ! -x "$TOOLS/bin/kubectl" ]; then
    fetch kubectl "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/$ARCH/kubectl" x "${SHA[kubectl-$ARCH]}"
    install -m755 "$TOOLS/dl/kubectl" "$TOOLS/bin/kubectl"
  fi
  # helm 3 for push/pull (a Helm 4 on PATH is shadowed); CI image alpine/helm is 3.17.3 too
  if [ ! -x "$TOOLS/bin/helm" ]; then
    fetch helm.tgz "https://get.helm.sh/helm-$HELM_VERSION-linux-$ARCH.tar.gz" x "${SHA[helm-$ARCH]}"
    tar -xzf "$TOOLS/dl/helm.tgz" -C "$TOOLS/dl" "linux-$ARCH/helm" && install -m755 "$TOOLS/dl/linux-$ARCH/helm" "$TOOLS/bin/helm"
  fi
  echo "kind $(kind version | cut -d' ' -f1-2), kubectl $(kubectl version --client -o json | jq -r .clientVersion.gitVersion), helm $(helm version --short), terraform $(terraform version -json | jq -r .terraform_version)"
}

in_registry() { curl -fs -o /dev/null -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json' "http://$REG/v2/${1%:*}/manifests/${1##*:}"; }
mirror_image() { # src dst-path
  in_registry "$2" && return 0
  docker pull -q "$1" >/dev/null && docker tag "$1" "$REG/$2" && docker push -q "$REG/$2" >/dev/null && echo "mirrored $2"
}

registry() {
  if ! docker inspect "$REG_NAME" >/dev/null 2>&1; then
    docker run -d --restart=always -p "127.0.0.1:$REG_PORT:5000" --name "$REG_NAME" "$REGISTRY_IMAGE" >/dev/null
  fi
  docker start "$REG_NAME" >/dev/null 2>&1 || true
  for _ in $(seq 30); do curl -fsS "http://$REG/v2/" >/dev/null 2>&1 && return 0; sleep 1; done
  die "registry not reachable on $REG"
}

cluster() {
  if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" --wait 180s
  fi
  kind export kubeconfig --name "$CLUSTER" --kubeconfig "$KUBECONFIG" >/dev/null
  docker network inspect kind -f '{{json .Containers}}' | grep -q "$REG_NAME" || docker network connect kind "$REG_NAME"
  # containerd mirror: localhost:$REG_PORT (the Terraform mirror host) and the upstream registries that operators pull
  # from by themselves resolve to the local registry (path preserved), like a CRI registry mirror in a real air gap.
  local node h
  for node in $(kind get nodes --name "$CLUSTER"); do
    for h in "localhost:$REG_PORT" quay.io ghcr.io docker.elastic.co; do
      docker exec "$node" mkdir -p "/etc/containerd/certs.d/$h"
      printf 'server = "http://%s:5000"\n[host."http://%s:5000"]\n  capabilities = ["pull", "resolve"]\n' "$REG_NAME" "$REG_NAME" |
        docker exec -i "$node" tee "/etc/containerd/certs.d/$h/hosts.toml" >/dev/null
    done
  done
}

mirror_all() {
  local i src dst
  for i in "${IMAGES[@]}"; do read -r src dst <<<"$i"; mirror_image "$src" "$dst"; done
  mkdir -p "$TOOLS/charts"
  local c name url ver
  for c in "${CHARTS[@]}"; do
    read -r name url ver <<<"$c"
    if curl -fsS "http://$REG/v2/charts/$name/tags/list" 2>/dev/null | grep -q "\"$ver\""; then continue; fi
    helm pull "$name" --repo "$url" --version "$ver" -d "$TOOLS/charts" >/dev/null
    helm push "$TOOLS/charts/$name-${ver#v}.tgz" "oci://$REG/charts" --plain-http >/dev/null 2>&1 ||
      helm push "$TOOLS/charts/$name-$ver.tgz" "oci://$REG/charts" --plain-http
    echo "pushed chart $name $ver"
  done
}

build_app() {
  local n
  for n in backend:backend/Dockerfile web:frontend/Dockerfile; do
    in_registry "roracle/${n%%:*}:$VERSION" && continue
    docker build -q -f "$REPO/${n#*:}" --build-arg VERSION="$VERSION" -t "$REG/roracle/${n%%:*}:$VERSION" "$REPO"
    docker push -q "$REG/roracle/${n%%:*}:$VERSION" >/dev/null
  done
}

secrets() {
  [ -f "$TOOLS/secrets.json" ] && return 0
  local r='openssl rand -hex 16'
  (umask 077; jq -n --arg db "$($r)" --arg es "$($r)"     --arg s1 "$($r)" --arg s2 "$($r)"     '{db_password:$db, es_app_password:$es,       s3_access_key:$s1, s3_secret_key:$s2}' >"$TOOLS/secrets.json")
}

cmd_up() {
  log tools; tools
  log registry; registry
  log cluster; cluster
  [ "$PROFILE" = ceph ] && { log "ceph loop devices"; ceph_loops_up; }
  log "mirror images + charts"; mirror_all
  log "build rOracle images"; build_app
}

gate_checks() { # fresh state: nothing is created in the cluster
  local gs="$TOOLS/gate.tfstate" out
  rm -f "$gs" "$gs.backup"
  # 1. a placeholder mirror leaves the gate failing: apply is refused, nothing created
  if out="$(tf apply -auto-approve "${tfargs[@]}" -state="$gs" -var mirror_registry=MIRROR.example.com 2>&1)"; then die "apply passed with a placeholder"; fi
  grep -q "Placeholders remain" <<<"$out" || { echo "$out" >&2; die "apply failed, but not at the placeholder gate"; }
  [ ! -s "$gs" ] || ! jq -e '.resources | map(select(.type != "terraform_data")) | length > 0' "$gs" >/dev/null || die "resources created despite gate"
  echo "gate: apply refused with placeholder (ok)"
  # 2. allow_placeholders=true passes the gate (only the gate is applied: dry-run semantics)
  tf apply -auto-approve "${tfargs[@]}" -state="$gs" -var mirror_registry=MIRROR.example.com -var allow_placeholders=true \
    -target=terraform_data.placeholder_gate >/dev/null || die "allow_placeholders=true did not pass the gate"
  echo "gate: allow_placeholders=true passes (ok)"
  if [ "$PROFILE" != ceph ]; then
  # 3. object_store=rook-ceph without mirrored charts/images/devices is refused (nothing created)
  if out="$(tf apply -auto-approve "${tfargs[@]}" -state="$gs" -var object_store=rook-ceph 2>&1)"; then die "rook-ceph applied without charts/images/devices"; fi
  grep -q "ceph.device_filter\|ceph.image\|rook_ceph" <<<"$out" || { echo "$out" >&2; die "rook-ceph refusal did not name ceph placeholders"; }
  echo "gate: object_store=rook-ceph without mirror values/devices refused (ok)"
  fi
  rm -f "$gs" "$gs.backup"
}

cmd_apply() {
  [ -f "$KUBECONFIG" ] || die "run 'up' first"
  secrets
  if [ "$PROFILE" = ceph ]; then ceph_write_tfvars; else
  log "s3 stand-in"
  kubectl apply -f "$HERE/s3-stub.yaml" >/dev/null
  kubectl -n rorac-s3 create secret generic s3-stub --from-env-file=<(jq -r '"access=\(.s3_access_key)\nsecret=\(.s3_secret_key)"' "$TOOLS/secrets.json") \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n rorac-s3 rollout status deploy/s3-stub --timeout=180s
  fi
  log "terraform init"; tf init -input=false >/dev/null
  log "gate checks"; gate_checks
  log "terraform plan"; tf plan "${tfargs[@]}" -state="$STATE" -out="$TOOLS/kind.tfplan" | tail -5
  log "terraform apply"; tf apply -auto-approve -input=false -state="$STATE" "$TOOLS/kind.tfplan" | tail -15
  rm -f "$TOOLS/kind.tfplan" # holds the secrets in clear text
}

wait_api() { # background port-forward + curl; $1 = svc, $2 = remote port, $3 = path
  local lp pf rc=1; lp=$((20000 + RANDOM % 20000))
  kubectl -n "$NS" port-forward "svc/$1" "$lp:$2" >/dev/null 2>&1 & pf=$!
  for _ in $(seq 30); do
    if curl -fs -o /dev/null "http://127.0.0.1:$lp$3" 2>/dev/null; then rc=0; break; fi; sleep 1
  done
  [ "$rc" = 0 ] && curl -sS "http://127.0.0.1:$lp$3" | head -c 200; echo
  kill "$pf" 2>/dev/null || true; wait "$pf" 2>/dev/null || true
  return "$rc"
}

cmd_verify() {
  [ -f "$KUBECONFIG" ] || die "run 'up' first"
  log "pods ready"
  local bad=""
  for _ in $(seq 60); do
    bad="$(kubectl get pods -A -o json | jq -r '.items[] | select(.status.phase != "Succeeded")
      | select(any(.status.conditions[]?; .type == "Ready" and .status != "True") or (.status.conditions == null))
      | "\(.metadata.namespace)/\(.metadata.name) \(.status.phase)"')"
    [ -z "$bad" ] && break; sleep 5
  done
  [ -z "$bad" ] || { echo "$bad" >&2; die "pods not Ready"; }
  kubectl get pods -A --no-headers | awk '{print $1}' | sort | uniq -c | tr '\n' ' '; echo
  log "migrate job"
  [ "$(kubectl -n $NS get job "$REL-migrate" -o jsonpath='{.status.succeeded}')" = 1 ] || die "migrate job not succeeded"
  log "api /healthz"; wait_api "$REL-api" 8000 /healthz || die "api /healthz"
  log "api /readyz (db, es, s3)"; wait_api "$REL-api" 8000 /readyz || die "api /readyz"
  log "web"; wait_api "$REL-web" 8080 / >/dev/null || die "web not served"; echo "web 200"
  log "images from the mirror only"
  local ext
  ext="$(kubectl get pods -A -o json | jq -r --arg r "$REG/" '.items[] | select(.metadata.namespace != "kube-system" and .metadata.namespace != "local-path-storage")
    | .spec.containers[], (.spec.initContainers // [])[] | .image | select(startswith($r) | not)' | sort -u)"
  [ -z "$ext" ] || { echo "$ext" >&2; die "images not from $REG"; }
  log "protect_data=true blocks destroy of the data tier"
  local out
  if out="$(tf destroy -auto-approve "${tfargs[@]}" -state="$STATE" 2>&1)"; then die "destroy succeeded with protect_data=true"; fi
  grep -q "prevent_destroy" <<<"$out" || { echo "$out" >&2; die "destroy failed, but not by prevent_destroy"; }
  kubectl -n "$NS" get cluster.postgresql.cnpg.io >/dev/null || die "data tier gone after blocked destroy"
  echo "OK: destroy refused (prevent_destroy), data tier intact"
  echo "VERIFY PASSED"
  [ "$PROFILE" != ceph ] || ceph_verify
}

cmd_destroy() {
  [ -f "$STATE" ] || { echo "no state: nothing to destroy"; return 0; }
  secrets
  # README: flip protect_data with state mv first, never a plain apply
  if tf state list -state="$STATE" 2>/dev/null | grep -q 'helm_release.protected\[0\]'; then
    tf state mv -state="$STATE" 'module.app[0].helm_release.protected[0]' 'module.app[0].helm_release.unprotected[0]'
  fi
  log "terraform destroy (protect_data=false)"
  if [ "$PROFILE" = ceph ]; then
    # Rook refuses to delete the object store while buckets/users exist (documented): bounded, `down` removes the cluster anyway
    TF_VAR_secret_data="$(cat "$TOOLS/secrets.json")" timeout 900 terraform -chdir="$TF_DIR" destroy -auto-approve "${tfargs[@]}" -state="$STATE" -var protect_data=false 2>&1 | tail -5 ||
      echo "terraform destroy incomplete (expected for Rook-Ceph with buckets); tearing the cluster down"
  else
    tf destroy -auto-approve "${tfargs[@]}" -state="$STATE" -var protect_data=false | tail -5
  fi
  kubectl delete ns rorac-s3 --ignore-not-found >/dev/null
}

cmd_down() {
  [ "$PROFILE" != ceph ] || ceph_loops_down
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG" 2>/dev/null || kind delete cluster --name "$CLUSTER" || true
  docker rm -f "$REG_NAME" >/dev/null 2>&1 || true
  rm -f "$STATE" "$STATE.backup" "$KUBECONFIG" "$TOOLS/kind.tfplan"
  echo "removed cluster $CLUSTER and registry $REG_NAME (tools cache kept in $TOOLS; delete it to reclaim space)"
}

case "${1:-}" in
  up) cmd_up ;; apply) cmd_apply ;; verify) cmd_verify ;; destroy) cmd_destroy ;; down) cmd_down ;;
  all)
    trap '[ "${KIND_E2E_KEEP:-0}" = 1 ] || cmd_down' EXIT
    cmd_up; cmd_apply; cmd_verify; cmd_destroy ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
echo "done in ${SECONDS}s"
