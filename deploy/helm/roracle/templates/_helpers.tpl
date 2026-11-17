{{- define "roracle.fullname" -}}
{{- $n := default .Chart.Name .Values.nameOverride -}}
{{- if .Values.fullnameOverride -}}{{ .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else if contains $n .Release.Name -}}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name $n | trunc 63 | trimSuffix "-" }}
{{- end -}}
{{- end -}}

{{- define "roracle.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "roracle.selector" -}}
app.kubernetes.io/name: {{ .root.Chart.Name }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .comp }}
{{- end -}}

{{/* Image ref: global.imageRegistry prefix; digest wins over tag; tag defaults to appVersion. args: root, img */}}
{{- define "roracle.image" -}}
{{- $r := .img.repository -}}
{{- with .root.Values.global.imageRegistry }}{{ $r = printf "%s/%s" (trimSuffix "/" .) $r }}{{ end -}}
{{- if .img.digest }}{{ printf "%s@%s" $r .img.digest }}{{ else }}{{ printf "%s:%s" $r (default .root.Chart.AppVersion .img.tag) }}{{ end -}}
{{- end -}}

{{/* Computed defaults for config entries the user left empty. Later tasks extend this (elastic, parsers). */}}
{{/* Worst-case Postgres client connections at full autoscale: replicas x (pool + overflow) for api and worker. */}}
{{- define "roracle.pgClients" -}}
{{- $api := .Values.api }}{{- $wk := .Values.worker }}{{- $c := .Values.config }}
{{- $apiMax := ternary $api.autoscaling.maxReplicas $api.replicas $api.autoscaling.enabled | int }}
{{- $wkMax := ternary $wk.autoscaling.maxReplicas $wk.replicas $wk.autoscaling.enabled | int }}
{{- add (mul $apiMax (add (int $c.ROR_DB_POOL_SIZE) (int $c.ROR_DB_MAX_OVERFLOW))) (mul $wkMax (add (int $c.ROR_WORKER_DB_POOL_SIZE) (int $c.ROR_WORKER_DB_MAX_OVERFLOW) 1)) | int }}
{{- end -}}

{{- define "roracle.endpoints" -}}
{{- if and (eq .Values.postgres.mode "cnpg") .Values.postgres.pooler.enabled }}
ROR_DB_DISABLE_PREPARED: "true"   # transaction pooling: no server-side prepared statements (any PgBouncer version)
{{- end }}
{{- if .Values.ingress.host }}
ROR_PUBLIC_BASE_URL: {{ printf "https://%s" .Values.ingress.host | quote }}
{{- end }}
{{- with .Values.rerank }}{{ if .url }}
ROR_RERANK_URL: {{ .url | quote }}
ROR_RERANK_API: {{ .api | quote }}
ROR_RERANK_MODEL: {{ .model | quote }}
{{- end }}{{ end }}
{{- $pf := printf "%s-" (include "roracle.fullname" .) }}
{{- with .Values.parsers.gotenberg }}{{ if .enabled }}
ROR_GOTENBERG_URL: {{ printf "http://%sgotenberg:%v" $pf .port | quote }}
{{- end }}{{ end }}
{{- with .Values.parsers.draftgotenberg }}{{ if .enabled }}
ROR_DRAFT_GOTENBERG_URL: {{ printf "http://%sdraftgotenberg:%v" $pf .port | quote }}
{{- end }}{{ end }}
{{- with .Values.parsers.docling }}{{ if .enabled }}
ROR_DOCLING_URL: {{ printf "http://%sdocling:%v" $pf .port | quote }}
{{- end }}{{ end }}
{{- with .Values.parsers.grobid }}{{ if .enabled }}
ROR_GROBID_URL: {{ printf "http://%sgrobid:%v" $pf .port | quote }}
{{- end }}{{ end }}
{{- if .Values.parsers.clamav.enabled }}
ROR_CLAMAV_HOST: {{ printf "%sclamav" $pf | quote }}
ROR_CLAMAV_PORT: {{ .Values.parsers.clamav.port | quote }}
{{- end }}
{{- end -}}

{{/* Operator-managed image ref: like roracle.image but the tag is mandatory (no appVersion default). args: root, img */}}
{{- define "roracle.dataImage" -}}
{{- $r := .img.repository -}}
{{- with .root.Values.global.imageRegistry }}{{ $r = printf "%s/%s" (trimSuffix "/" .) $r }}{{ end -}}
{{- if .img.digest }}{{ printf "%s@%s" $r .img.digest }}{{ else }}{{ printf "%s:%s" $r (required "image tag is required" .img.tag) }}{{ end -}}
{{- end -}}

{{/* Final ROR_* map: user config + computed defaults, empties dropped, required keys enforced. */}}
{{- define "roracle.config" -}}
{{- $m := merge (deepCopy .Values.config) (include "roracle.endpoints" . | fromYaml) -}}
{{- range $k := list "ROR_S3_ENDPOINT" "ROR_OIDC_ISSUER" "ROR_OIDC_JWKS_URL" }}{{ $_ := required (printf "config.%s is required" $k) (get $m $k) }}{{ end -}}
{{- $out := dict -}}
{{- range $k, $v := $m }}{{ if and (not (kindIs "invalid" $v)) (ne (toString $v) "") }}{{ $_ := set $out $k (kindIs "slice" $v | ternary (join "," $v) (toString $v)) }}{{ end }}{{ end -}}
{{- toYaml $out -}}
{{- end -}}

{{/* Inline name/value env (for the pre-install hook Job, where the ConfigMap does not exist yet). */}}
{{- define "roracle.configEnv" -}}
{{- range $k, $v := include "roracle.config" . | fromYaml }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}

{{- define "roracle.secretEnv" -}}
{{- $s := required "secrets.existingSecret is required" .Values.secrets.existingSecret -}}
{{- $k := .Values.secrets.keys }}
- {name: ROR_DATABASE_URL, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $k.databaseUrl | quote }}}}}
{{- $s3 := default $s .Values.secrets.s3Secret }}
- {name: ROR_S3_ACCESS_KEY, valueFrom: {secretKeyRef: {name: {{ $s3 | quote }}, key: {{ $k.s3AccessKey | quote }}}}}
- {name: ROR_S3_SECRET_KEY, valueFrom: {secretKeyRef: {name: {{ $s3 | quote }}, key: {{ $k.s3SecretKey | quote }}}}}
- {name: ROR_ELASTIC_URL, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $k.elasticUrl | quote }}}}}
{{- range $env, $key := dict "ROR_ELASTIC_USERNAME" $k.elasticUsername "ROR_ELASTIC_PASSWORD" $k.elasticPassword "ROR_DATABASE_PASSWORD" $k.databasePassword }}
{{- if $key }}
- {name: {{ $env }}, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $key | quote }}, optional: true}}}
{{- end }}
{{- end }}
{{- if .Values.secrets.rerankApiKeyEnabled }}
- {name: ROR_RERANK_API_KEY, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $k.rerankApiKey | quote }}}}}
{{- end }}
{{- if .Values.secrets.llmApiKeyEnabled }}
- {name: ROR_LLM_API_KEY, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $k.llmApiKey | quote }}}}}
{{- end }}
{{- end -}}

{{/* OTel env, only when an endpoint is set. The image wraps api/worker in opentelemetry-instrument when set. args: root, comp */}}
{{- define "roracle.otelEnv" -}}
{{- with .root.Values.observability.otel.endpoint }}
- {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: {{ . | quote }}}
- {name: OTEL_EXPORTER_OTLP_PROTOCOL, value: http/protobuf}
- {name: OTEL_SERVICE_NAME, value: {{ printf "roracle-%s" $.comp | quote }}}
- {name: OTEL_RESOURCE_ATTRIBUTES, value: {{ printf "service.version=%s" $.root.Chart.AppVersion | quote }}}
{{- end }}
{{- end -}}

{{- define "roracle.pullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets: {{ toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Deployment (+ Service when .port) for one component. args: root, comp, v (component values), img,
command (list, optional), port, healthPort (worker /healthz+/metrics, optional), metricsPort (annotation, optional), env (yaml list string), tmp (emptyDir size), probe (bool), runAsUser (optional).
*/}}
{{- define "roracle.workload" -}}
{{- $root := .root -}}
{{- $name := printf "%s-%s" (include "roracle.fullname" $root) .comp -}}
{{- $sel := include "roracle.selector" (dict "root" $root "comp" .comp) -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $name }}
  labels: {{ include "roracle.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .comp }}
spec:
  {{- with .minReady }}
  minReadySeconds: {{ . }}
  {{- end }}
  {{- if not .v.autoscaling.enabled }}
  replicas: {{ .v.replicas }}
  {{- end }}
  selector:
    matchLabels: {{ $sel | nindent 6 }}
  template:
    metadata:
      labels: {{ include "roracle.labels" $root | nindent 8 }}
        app.kubernetes.io/component: {{ .comp }}
      annotations:
        checksum/config: {{ include "roracle.config" $root | sha256sum }}
        {{- if and .metricsPort $root.Values.metrics.enabled }}
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ .metricsPort | quote }}
        prometheus.io/path: /metrics
        {{- end }}
    spec:
      serviceAccountName: {{ include "roracle.saName" $root }}
      automountServiceAccountToken: false
      terminationGracePeriodSeconds: {{ default 30 .v.terminationGracePeriodSeconds }}
      securityContext:
        {{- toYaml $root.Values.podSecurityContext | nindent 8 }}
        {{- with .runAsUser }}
        runAsUser: {{ . }}
        {{- end }}
      {{- include "roracle.pullSecrets" $root | nindent 6 }}
      {{- if $root.Values.topologySpread.enabled }}
      topologySpreadConstraints:
        {{- range $key := list "topology.kubernetes.io/zone" "kubernetes.io/hostname" }}
        - maxSkew: 1
          topologyKey: {{ $key }}
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels: {{ $sel | nindent 14 }}
        {{- end }}
      {{- end }}
      {{- with .v.nodeSelector }}
      nodeSelector: {{ toYaml . | nindent 8 }}
      {{- end }}
      {{- with .v.tolerations }}
      tolerations: {{ toYaml . | nindent 8 }}
      {{- end }}
      {{- if .ca }}{{ include "roracle.caInit" $root | nindent 6 }}{{ end }}
      containers:
        - name: {{ .comp }}
          image: {{ include "roracle.image" (dict "root" $root "img" .img) }}
          imagePullPolicy: {{ .img.pullPolicy }}
          {{- with .command }}
          command: {{ toYaml . | nindent 12 }}
          {{- end }}
          {{- $env := print (.env | default "") (ternary (include "roracle.caEnv" $root) "" (default false .ca)) }}
          {{- if $env }}
          env: {{ $env | nindent 12 }}
          {{- end }}
          {{- if .configMap }}
          envFrom:
            - configMapRef: {name: {{ include "roracle.fullname" $root }}-env}
          {{- end }}
          {{- if or .port .healthPort }}
          ports:
            {{- with .port }}
            - {name: http, containerPort: {{ . }}}
            {{- end }}
            {{- with .healthPort }}
            - {name: health, containerPort: {{ . }}}
            {{- end }}
          {{- end }}
          {{- if .probe }}
          startupProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 5, failureThreshold: 30}
          readinessProbe: {httpGet: {path: /readyz, port: http}, periodSeconds: 10}
          livenessProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 20}
          {{- end }}
          {{- if .healthPort }}
          livenessProbe: {httpGet: {path: /healthz, port: health}, periodSeconds: 30, failureThreshold: 3}
          {{- end }}
          resources: {{ toYaml .v.resources | nindent 12 }}
          securityContext: {{ toYaml $root.Values.containerSecurityContext | nindent 12 }}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
            {{- if .ca }}{{ include "roracle.caMounts" $root | nindent 12 }}{{ end }}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: {{ .tmp }}}
        {{- if .ca }}{{ include "roracle.caVolumes" (dict "root" $root "optional" false) | nindent 8 }}{{ end }}
{{- if .port }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $name }}
  labels: {{ include "roracle.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .comp }}
spec:
  selector: {{ $sel | nindent 4 }}
  ports:
    - {name: http, port: {{ .port }}, targetPort: http}
{{- end }}
{{- end -}}

{{- define "roracle.saName" -}}
{{- if .Values.serviceAccount.name }}{{ .Values.serviceAccount.name }}{{ else if .Values.serviceAccount.create }}{{ include "roracle.fullname" . }}{{ else }}default{{ end -}}
{{- end -}}

{{- define "roracle.extraEnv" -}}
{{- with .extraEnv }}{{ toYaml . | nindent 0 }}{{ end -}}
{{- end -}}

{{/* ---- Interim CA bundle (see values caBundle). Empty output unless a TLS CA is in play. ---- */}}
{{- define "roracle.caEnabled" -}}
{{- if or (and (eq .Values.elasticsearch.mode "eck") .Values.elasticsearch.tls) .Values.caBundle.extraSecrets }}true{{ end -}}
{{- end -}}

{{/* Init container + volumes + mounts + env. Main containers get the merged bundle over certifi's cacert.pem. */}}
{{- define "roracle.caInit" -}}
{{- if include "roracle.caEnabled" . }}
initContainers:
  - name: ca-bundle
    image: {{ include "roracle.image" (dict "root" . "img" .Values.image.backend) }}
    imagePullPolicy: {{ .Values.image.backend.pullPolicy }}
    command: [sh, -ec, '{ cat "$(python -c "import certifi;print(certifi.where())")"; for f in /ca/*.crt; do [ ! -f "$f" ] || cat "$f"; done; } > /bundle/ca-bundle.pem']
    resources:
      requests: {cpu: 50m, memory: 64Mi}
      limits: {memory: 128Mi}
    securityContext: {{ toYaml .Values.containerSecurityContext | nindent 6 }}
    volumeMounts:
      - {name: ca-src, mountPath: /ca, readOnly: true}
      - {name: ca-bundle, mountPath: /bundle}
{{- end }}
{{- end -}}

{{/* args: root, optional. The ES CA secret is optional only in the migrate hook (it may not exist yet); api/worker wait for it. */}}
{{- define "roracle.caVolumes" -}}
{{- $o := .optional -}}
{{- with .root }}
{{- if include "roracle.caEnabled" . }}
- name: ca-bundle
  emptyDir: {sizeLimit: 1Mi}
- name: ca-src
  projected:
    sources:
      {{- if and (eq .Values.elasticsearch.mode "eck") .Values.elasticsearch.tls }}
      - secret: {name: {{ include "roracle.fullname" . }}-es-http-certs-public, optional: {{ $o }}, items: [{key: ca.crt, path: es.crt}]}
      {{- end }}
      {{- range $i, $x := .Values.caBundle.extraSecrets }}
      - secret: {name: {{ $x.name | quote }}, items: [{key: {{ default "ca.crt" $x.key | quote }}, path: {{ printf "extra-%d.crt" $i }}}]}
      {{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "roracle.caMounts" -}}
{{- if include "roracle.caEnabled" . }}
- {name: ca-bundle, mountPath: {{ .Values.caBundle.certifiPath | quote }}, subPath: ca-bundle.pem, readOnly: true}
{{- end }}
{{- end -}}

{{- define "roracle.caEnv" -}}
{{- if include "roracle.caEnabled" . }}
{{- range $k := list "SSL_CERT_FILE" "REQUESTS_CA_BUNDLE" "AWS_CA_BUNDLE" }}
- {name: {{ $k }}, value: {{ $.Values.caBundle.certifiPath | quote }}}
{{- end }}
{{- /* explicit paths for the ES client (ca_certs) and boto3 (verify): same merged bundle */}}
{{- range $k := list "ROR_ELASTIC_CA_CERTS" "ROR_S3_CA_BUNDLE" }}
- {name: {{ $k }}, value: {{ $.Values.caBundle.certifiPath | quote }}}
{{- end }}
{{- end }}
{{- end -}}

{{/* NetworkPolicy peers: app pods (api/worker/migrate/es-snapshot jobs) in this release. */}}
{{- define "roracle.appPeer" -}}
- podSelector:
    matchLabels: {app.kubernetes.io/name: {{ .Chart.Name }}, app.kubernetes.io/instance: {{ .Release.Name | quote }}}
    matchExpressions:
      - {key: app.kubernetes.io/component, operator: In, values: [api, worker, migrate, es-snapshot]}
{{- end -}}

{{- define "roracle.nsPeer" -}}
- namespaceSelector: {matchLabels: {kubernetes.io/metadata.name: {{ . | quote }}}}
{{- end -}}

{{/* Bucket name from an s3:// destination path. */}}
{{- define "roracle.pgBucket" -}}
{{ .Values.postgres.backup.destinationPath | trimPrefix "s3://" | splitList "/" | first }}
{{- end -}}

{{/* ES S3 client protocol: explicit value, else https. */}}
{{- define "roracle.snapProtocol" -}}
{{- default "https" .Values.elasticsearch.snapshot.protocol -}}
{{- end -}}

{{/* "1100M" / "2G" / "512K" / "1048576" -> bytes (int64). */}}
{{- define "roracle.bytes" -}}
{{- $s := toString . | lower -}}
{{- if hasSuffix "g" $s }}{{ mul (int64 (trimSuffix "g" $s)) 1073741824 }}
{{- else if hasSuffix "m" $s }}{{ mul (int64 (trimSuffix "m" $s)) 1048576 }}
{{- else if hasSuffix "k" $s }}{{ mul (int64 (trimSuffix "k" $s)) 1024 }}
{{- else }}{{ int64 $s }}{{ end -}}
{{- end -}}

{{/* Worker entrypoint: wait (bounded, logged every 5 s) until alembic_version = this image's head, then exec the worker.
Why: the bundled-CNPG migrate hook is post-install, which Helm (--wait) runs only after the release is Ready, while the worker
crashes at startup without the schema (install deadlock). During the wait a stub answers 200 on the worker health port, so
liveness stays green and the pod counts as Ready. 1500 s = migrate.activeDeadlineSeconds. */}}
{{- define "roracle.workerStartPy" -}}
import os, sys, time, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from roracle.config import get_settings
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path.startswith("/healthz")
        self.send_response(200 if ok else 404); self.end_headers()
        if ok:
            self.wfile.write(b"waiting for schema")
    def log_message(self, *a):
        pass
port = int(os.environ.get("ROR_WORKER_HEALTH_PORT") or 0)
srv = HTTPServer(("0.0.0.0", port), H) if port else None
if srv:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
script = ScriptDirectory.from_config(Config("alembic.ini"))
head = script.get_current_head()
known = {r.revision for r in script.walk_revisions()}
engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 5})
end = time.time() + 1500
while True:
    try:
        with engine.connect() as c:
            cur = c.execute(text("select version_num from alembic_version")).scalar()
    except Exception as e:
        cur = None
        why = "unavailable (%s)" % type(e).__name__
    else:
        why = "at %s" % cur
    # proceed at head, or when the DB is NEWER than this image (rollback/old tag: expand-first schema is compatible)
    if cur == head or (cur is not None and cur not in known):
        print("schema ok: db %s, image head %s" % (why, head), flush=True)
        break
    if time.time() > end:
        print("TIMEOUT waiting for migrations: db %s, image head %s" % (why, head), file=sys.stderr, flush=True)
        sys.exit(1)
    print("waiting for migrations: db %s, image head %s" % (why, head), flush=True)
    time.sleep(5)
if srv:
    srv.shutdown(); srv.server_close()
os.execvp("python", ["python", "-m", "roracle", "worker"])
{{- end -}}
