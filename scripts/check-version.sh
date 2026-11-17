#!/bin/sh
# Fail unless VERSION == Chart.yaml version == appVersion == backend/pyproject.toml == frontend/package.json.
root=$(cd "$(dirname "$0")/.." && pwd)
v=$(tr -d '[:space:]' < "$root/VERSION")
c="$root/deploy/helm/roracle/Chart.yaml"
cv=$(sed -n 's/^version: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' "$c")
av=$(sed -n 's/^appVersion: *"\{0,1\}\([^"]*\)"\{0,1\} *$/\1/p' "$c")
pv=$(sed -n 's/^version = "\([^"]*\)"$/\1/p' "$root/backend/pyproject.toml" | head -n 1)
fv=$(sed -n 's/^ *"version": *"\([^"]*\)",\{0,1\}$/\1/p' "$root/frontend/package.json" | head -n 1)
if [ "$v" != "$cv" ] || [ "$v" != "$av" ] || [ "$v" != "$pv" ] || [ "$v" != "$fv" ]; then
  echo "version mismatch: VERSION=$v Chart version=$cv appVersion=$av pyproject=$pv package.json=$fv" >&2
  exit 1
fi
echo "version ok: $v"
