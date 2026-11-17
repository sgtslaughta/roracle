#!/bin/sh
# Writes the include files the server block expects (output dir must be writable: emptyDir/tmpfs on /tmp).
set -eu
out=/tmp/nginx
mkdir -p "$out"

# CSP needs the runtime OIDC origin
sed "s#\${ROR_OIDC_ORIGIN}#${ROR_OIDC_ORIGIN}#g" /etc/nginx/security-headers.conf > "$out/security-headers.conf"

if [ -n "${API_UPSTREAM:-}" ]; then
  up="${API_UPSTREAM%/}"
  cat > "$out/api.inc" <<EOT
# Prometheus /metrics is unauthenticated: never reachable through the public proxy
location ^~ /api/metrics { return 404; }
# readyz fans out to DB+ES+S3 and reveals dependency state: kubelet probes the pod directly, not this proxy
location = /api/readyz { return 404; }
location /api/ {
  proxy_pass $up/;
  proxy_http_version 1.1;
  proxy_set_header Host \$host;
  proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto \$ror_fwd_proto;
  proxy_set_header X-Forwarded-Host \$host;
  proxy_set_header X-Forwarded-Prefix /api;
  proxy_hide_header Content-Security-Policy;
  proxy_hide_header X-Frame-Options;
  proxy_hide_header X-Content-Type-Options;
  proxy_hide_header Referrer-Policy;
  proxy_hide_header Permissions-Policy;
  proxy_hide_header Cross-Origin-Opener-Policy;
  proxy_request_buffering off;
  proxy_read_timeout 300s;
  include $out/security-headers.conf;
}
EOT
else
  cat > "$out/api.inc" <<EOT
location /api/ {
  include $out/security-headers.conf;
  return 404;
}
EOT
fi
