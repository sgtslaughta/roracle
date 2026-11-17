# shellcheck shell=bash
# Sourced by kind-e2e.sh (profile ceph): LIVE Rook-Ceph rehearsal. LOCAL ONLY: needs a privileged kind node with loop devices
# (not for shared or Docker-in-Docker runners). OSDs = 3 loop devices backed by sparse files INSIDE the kind node; every device and
# file is recorded in $TOOLS/loops.list and detached/removed by `down` (never touches other loop devices).
NODE=rorac-kind-control-plane
LOOPS="$TOOLS/loops.list"
OSD_SIZE=8G

ceph_loops_up() {
  [ -s "$LOOPS" ] && return 0
  local i dev f
  for i in 1 2 3; do
    f=/var/lib/rorac-osd$i.img
    docker exec "$NODE" truncate -s "$OSD_SIZE" "$f"
    # the node's /dev is a private tmpfs: a loop device allocated later has no node ("lost"): create it, then attach
    dev="$(docker exec "$NODE" losetup -f | awk '{print $1}')"
    docker exec "$NODE" sh -c "[ -b $dev ] || mknod $dev b 7 \${dev#/dev/loop}"
    docker exec "$NODE" losetup "$dev" "$f" || die "losetup failed (no loop device support?)"
    echo "$dev $f" >>"$LOOPS"
  done
  echo "loop OSD devices: $(tr '\n' ' ' <"$LOOPS")"
}

ceph_loops_down() { # detach exactly what we attached, then remove the files; safe to call repeatedly
  [ -s "$LOOPS" ] || return 0
  local dev f
  if docker inspect "$NODE" >/dev/null 2>&1; then
    while read -r dev f; do
      docker exec "$NODE" sh -c "losetup -d $dev; rm -f $f" || true
    done <"$LOOPS"
    # nothing of ours may stay attached
    while read -r _ f; do
      ! docker exec "$NODE" losetup -j "$f" 2>/dev/null | grep -q . || echo "WARNING: $f still attached" >&2
    done <"$LOOPS"
  else
    echo "WARNING: node gone before detaching; check 'losetup -a' for $(cut -d' ' -f2 "$LOOPS" | tr '\n' ' ')" >&2
  fi
  rm -f "$LOOPS"
}

ceph_write_tfvars() { # ceph = {...} with the real device names (a whole-variable override: tfvars files do not merge)
  local devs
  devs="$(awk '{sub("/dev/","",$1); printf "%s{ name = \"%s\" }", (NR>1?", ":""), $1}' "$LOOPS")"
  cat >"$TOOLS/ceph-osd.tfvars" <<TF
ceph = {
  image                        = { tag = "v19.2.3" }
  mon_count                    = 1
  mgr_count                    = 1
  allow_multiple_mons_per_node = true
  failure_domain               = "osd"
  nodes                        = [{ name = "$NODE", devices = [$devs] }]
  data_pool                    = { mode = "erasure", data_chunks = 2, coding_chunks = 1 }
  rgw                          = { instances = 1, cert_manager_issuer = "self-signed-ca", resources = { requests = { cpu = "200m", memory = "512Mi" }, limits = { memory = "1Gi" } } }
  resources = {
    mon = { requests = { cpu = "100m", memory = "512Mi" }, limits = { memory = "1Gi" } }
    mgr = { requests = { cpu = "100m", memory = "512Mi" }, limits = { memory = "1Gi" } }
    osd = { requests = { cpu = "200m", memory = "1Gi" }, limits = { memory = "2Gi" } }
  }
}
TF
}

ceph_checks_job() { # $1 = PHASE app|es ; runs test/ceph-checks.py in the app namespace as a pod that the chart policies treat as an app pod
  local phase=$1 tls_ep
  tls_ep="https://rook-ceph-rgw-roracle.rook-ceph.svc:443"
  kubectl -n "$NS" delete job rehearsal-checks --ignore-not-found >/dev/null
  kubectl -n "$NS" create configmap rehearsal-checks --from-file=ceph-checks.py="$HERE/ceph-checks.py" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  # test-only copy of the admin keys into the app namespace (deleted afterwards)
  kubectl -n rook-ceph get secret rook-ceph-object-user-roracle-roracle-admin -o json |
    jq '{apiVersion:"v1",kind:"Secret",metadata:{name:"rehearsal-admin",namespace:"'"$NS"'"},data:.data}' | kubectl apply -f - >/dev/null
  local img; img="$(kubectl -n "$NS" get deploy "$REL-api" -o jsonpath='{.spec.template.spec.containers[0].image}')"
  kubectl apply -f - >/dev/null <<YAML
apiVersion: batch/v1
kind: Job
metadata: {name: rehearsal-checks, namespace: $NS}
spec:
  backoffLimit: 0
  template:
    metadata:
      labels: {app.kubernetes.io/name: roracle, app.kubernetes.io/instance: $REL, app.kubernetes.io/component: migrate}
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      securityContext: {runAsNonRoot: true, runAsUser: 10001}
      containers:
        - name: checks
          image: $img
          command: [python, /checks/ceph-checks.py]
          env:
            - {name: PHASE, value: $phase}
            - {name: S3_ENDPOINT, value: "$tls_ep"}
            - {name: AWS_CA_BUNDLE, value: /ca/ca.crt}
            - {name: AWS_REQUEST_CHECKSUM_CALCULATION, value: when_required}
            - {name: AWS_RESPONSE_CHECKSUM_VALIDATION, value: when_required}
            - {name: BUCKETS, value: '{"app":"roracle","pg":"roracle-pg-backup","es":"roracle-es-snapshots"}'}
            - {name: APP_AK, valueFrom: {secretKeyRef: {name: roracle-s3, key: ROR_S3_ACCESS_KEY}}}
            - {name: APP_SK, valueFrom: {secretKeyRef: {name: roracle-s3, key: ROR_S3_SECRET_KEY}}}
            - {name: PG_AK, valueFrom: {secretKeyRef: {name: roracle-pg-backup-s3, key: ACCESS_KEY_ID}}}
            - {name: PG_SK, valueFrom: {secretKeyRef: {name: roracle-pg-backup-s3, key: ACCESS_SECRET_KEY}}}
            - {name: ES_AK, valueFrom: {secretKeyRef: {name: roracle-es-s3, key: s3.client.default.access_key}}}
            - {name: ES_SK, valueFrom: {secretKeyRef: {name: roracle-es-s3, key: s3.client.default.secret_key}}}
            - {name: ADMIN_AK, valueFrom: {secretKeyRef: {name: rehearsal-admin, key: AccessKey}}}
            - {name: ADMIN_SK, valueFrom: {secretKeyRef: {name: rehearsal-admin, key: SecretKey}}}
          volumeMounts: [{name: checks, mountPath: /checks}, {name: ca, mountPath: /ca}]
          resources: {requests: {cpu: 50m, memory: 128Mi}, limits: {memory: 256Mi}}
      volumes:
        - {name: checks, configMap: {name: rehearsal-checks}}
        - {name: ca, secret: {secretName: roracle-s3-ca}}
YAML
  local rc=1
  for _ in $(seq 60); do
    case "$(kubectl -n "$NS" get job rehearsal-checks -o jsonpath='{.status.succeeded}/{.status.failed}')" in
      1/*) rc=0; break ;; */1) break ;; esac
    sleep 3
  done
  kubectl -n "$NS" logs job/rehearsal-checks || true
  kubectl -n "$NS" delete job/rehearsal-checks secret/rehearsal-admin configmap/rehearsal-checks --ignore-not-found >/dev/null
  return "$rc"
}

es_curl() { # method path ; elastic password via stdin (-K -), never argv
  local pw; pw="$(kubectl -n "$NS" get secret "$REL-es-elastic-user" -o jsonpath='{.data.elastic}' | base64 -d)"
  printf 'user = "elastic:%s"\n' "$pw" | kubectl -n "$NS" exec -i "$REL-es-default-0" -c elasticsearch -- curl -sk -K - -X "$1" "https://localhost:9200$2"
}

ceph_verify() {
  log "ceph: cluster + object store"
  local h; h="$(kubectl -n rook-ceph get cephcluster -o jsonpath='{.items[0].status.ceph.health}')"
  echo "ceph health: $h"; [[ "$h" == HEALTH_OK || "$h" == HEALTH_WARN ]] || die "ceph health $h"
  local op; op="$(kubectl -n rook-ceph get cephobjectstore roracle -o jsonpath='{.status.phase}')"
  [[ "$op" == Ready || "$op" == Connected ]] || die "object store phase $op"
  kubectl -n rook-ceph get cephobjectstoreuser,jobs.batch 2>/dev/null | head -12
  log "ceph: S3 access, object lock, writer/app denials"; ceph_checks_job app || die "ceph checks (app) failed"
  log "ceph: CNPG backup to RGW"
  kubectl -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata: {name: rehearsal-1}
spec: {cluster: {name: $REL-pg}, method: plugin, pluginConfiguration: {name: barman-cloud.cloudnative-pg.io}}
YAML
  local ph=""
  for _ in $(seq 60); do
    ph="$(kubectl -n "$NS" get backup rehearsal-1 -o jsonpath='{.status.phase}')"
    [ "$ph" = completed ] && break; [ "$ph" = failed ] && break; sleep 5
  done
  [ "$ph" = completed ] || { kubectl -n "$NS" get backup rehearsal-1 -o yaml | tail -15; die "CNPG backup phase '$ph'"; }
  echo "CNPG backup completed"
  log "ceph: ES snapshot repository verify, repository analysis, SLM create + delete"
  es_curl POST "/_snapshot/roracle-s3/_verify" | grep -q '"nodes"' || die "ES repository verify"
  # one retry: the very first analysis once failed with a spurious RGW BucketAlreadyExists (409) and passed on all later runs (README)
  local an try
  for try in 1 2; do
    an="$(es_curl POST "/_snapshot/roracle-s3/_analyze?blob_count=20&max_blob_size=1mb&timeout=180s")"
    if echo "$an" | grep -q '"summary"' && ! echo "$an" | grep -q '"error"'; then break; fi
    echo "analysis attempt $try failed: $(echo "$an" | head -c 300)"
    [ "$try" = 2 ] && die "ES repository analysis failed twice"
  done
  echo "repository analysis OK"
  local sn; sn="$(es_curl POST "/_slm/policy/roracle-daily/_execute" | jq -r .snapshot_name)"; [ -n "$sn" ] && [ "$sn" != null ] || die "SLM execute"
  for _ in $(seq 60); do
    [ "$(es_curl GET "/_snapshot/roracle-s3/$sn" | jq -r '.snapshots[0].state')" = SUCCESS ] && break; sleep 3
  done
  [ "$(es_curl GET "/_snapshot/roracle-s3/$sn" | jq -r '.snapshots[0].state')" = SUCCESS ] || die "snapshot $sn not SUCCESS"
  es_curl DELETE "/_snapshot/roracle-s3/$sn" | grep -q '"acknowledged":true' || die "snapshot delete"
  echo "SLM snapshot $sn created and deleted"
  log "ceph: delete markers / versions in the backup buckets"; ceph_checks_job es || die "ceph checks (es) failed"
  echo "CEPH VERIFY PASSED"
}
