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
location /api/ {
  proxy_pass $up/;
  proxy_http_version 1.1;
  proxy_set_header Host \$host;
  proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto \$http_x_forwarded_proto;
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
