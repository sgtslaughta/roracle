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

{{/* Computed defaults for config entries the user left empty. Later tasks extend this (elastic, parsers, minio). */}}
{{- define "roracle.endpoints" -}}
{{- if .Values.ingress.host }}
ROR_PUBLIC_BASE_URL: {{ printf "https://%s" .Values.ingress.host | quote }}
{{- end }}
{{- end -}}

{{/* Final ROR_* map: user config + computed defaults, empties dropped, required keys enforced. */}}
{{- define "roracle.config" -}}
{{- $m := merge (deepCopy .Values.config) (include "roracle.endpoints" . | fromYaml) -}}
{{- range $k := list "ROR_S3_ENDPOINT" "ROR_OIDC_ISSUER" "ROR_OIDC_JWKS_URL" }}{{ $_ := required (printf "config.%s is required" $k) (get $m $k) }}{{ end -}}
{{- $out := dict -}}
{{- range $k, $v := $m }}{{ if $v }}{{ $_ := set $out $k (toString $v) }}{{ end }}{{ end -}}
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
- {name: ROR_S3_ACCESS_KEY, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $k.s3AccessKey | quote }}}}}
- {name: ROR_S3_SECRET_KEY, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $k.s3SecretKey | quote }}}}}
{{- if .Values.secrets.llmApiKeyEnabled }}
- {name: ROR_LLM_API_KEY, valueFrom: {secretKeyRef: {name: {{ $s | quote }}, key: {{ $k.llmApiKey | quote }}}}}
{{- end }}
{{- end -}}

{{/* OTel env, only when an endpoint is set. Backend instrumentation lands in P11b. args: root, comp */}}
{{- define "roracle.otelEnv" -}}
{{- with .root.Values.observability.otel.endpoint }}
- {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: {{ . | quote }}}
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
command (list, optional), port, env (yaml list string), tmp (emptyDir size), probe (bool), runAsUser (optional).
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
    spec:
      serviceAccountName: {{ include "roracle.fullname" $root }}
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
      containers:
        - name: {{ .comp }}
          image: {{ include "roracle.image" (dict "root" $root "img" .img) }}
          imagePullPolicy: {{ .img.pullPolicy }}
          {{- with .command }}
          command: {{ toYaml . | nindent 12 }}
          {{- end }}
          {{- with .env }}
          env: {{ . | nindent 12 }}
          {{- end }}
          {{- if .configMap }}
          envFrom:
            - configMapRef: {name: {{ include "roracle.fullname" $root }}-env}
          {{- end }}
          {{- if .port }}
          ports:
            - {name: http, containerPort: {{ .port }}}
          {{- end }}
          {{- if .probe }}
          startupProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 5, failureThreshold: 30}
          readinessProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 10}
          livenessProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 20}
          {{- end }}
          resources: {{ toYaml .v.resources | nindent 12 }}
          securityContext: {{ toYaml $root.Values.containerSecurityContext | nindent 12 }}
          volumeMounts:
            - {name: tmp, mountPath: /tmp}
      volumes:
        - name: tmp
          emptyDir: {sizeLimit: {{ .tmp }}}
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
