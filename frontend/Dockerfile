# Build context: repo root.  docker build -f frontend/Dockerfile --build-arg VERSION=$(cat VERSION) -t roracle/web:dev .
ARG REGISTRY_MIRROR=""

FROM ${REGISTRY_MIRROR}node:22-slim AS build
ARG NPM_CONFIG_REGISTRY=""
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY} CI=1
# corepack downloads pnpm via COREPACK_NPM_REGISTRY (set below from NPM_CONFIG_REGISTRY when given).
# Mirrors that re-sign packages may also need COREPACK_INTEGRITY_KEYS=0 (build-arg/env) to skip signature checks.
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN if [ -n "$NPM_CONFIG_REGISTRY" ]; then export COREPACK_NPM_REGISTRY="$NPM_CONFIG_REGISTRY"; fi \
 && corepack enable && corepack pnpm install --frozen-lockfile
COPY frontend/ ./
# Placeholders are swapped at request time by nginx sub_filter (see nginx/default.conf.template).
ENV VITE_OIDC_AUTHORITY=__ROR_OIDC_AUTHORITY__ VITE_OIDC_CLIENT_ID=__ROR_OIDC_CLIENT_ID__
RUN corepack pnpm build && corepack pnpm check:dist \
 && grep -rl __ROR_OIDC_AUTHORITY__ dist/assets >/dev/null \
 && grep -rl __ROR_OIDC_CLIENT_ID__ dist/assets >/dev/null

FROM ${REGISTRY_MIRROR}nginxinc/nginx-unprivileged:1.27-alpine
ARG VERSION=0.0.0
LABEL org.opencontainers.image.title="roracle-web" org.opencontainers.image.version="${VERSION}"
# Runtime config: ROR_OIDC_AUTHORITY (required), ROR_OIDC_CLIENT_ID, ROR_OIDC_ORIGIN, API_UPSTREAM (optional)
ENV NGINX_ENVSUBST_OUTPUT_DIR=/tmp/nginx/conf.d
USER root
RUN rm -f /etc/nginx/conf.d/default.conf
COPY frontend/nginx/nginx.conf /etc/nginx/nginx.conf
COPY frontend/nginx/security-headers.conf /etc/nginx/security-headers.conf
COPY frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY frontend/nginx/05-ror-env.envsh /docker-entrypoint.d/05-ror-env.envsh
COPY frontend/nginx/25-api-proxy.sh /docker-entrypoint.d/25-api-proxy.sh
RUN chmod 0755 /docker-entrypoint.d/05-ror-env.envsh /docker-entrypoint.d/25-api-proxy.sh
COPY --from=build /app/dist /usr/share/nginx/html
USER 101
EXPOSE 8080
