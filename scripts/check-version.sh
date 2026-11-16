#!/bin/sh
# Fail unless VERSION == Chart.yaml version == appVersion.
root=$(cd "$(dirname "$0")/.." && pwd)
v=$(tr -d '[:space:]' < "$root/VERSION")
c="$root/deploy/helm/roracle/Chart.yaml"
cv=$(sed -n 's/^version: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' "$c")
av=$(sed -n 's/^appVersion: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' "$c")
if [ "$v" != "$cv" ] || [ "$v" != "$av" ]; then
  echo "version mismatch: VERSION=$v Chart version=$cv appVersion=$av" >&2
  exit 1
fi
echo "version ok: $v"
