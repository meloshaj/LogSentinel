# node:22.14.0-alpine3.21; immutable multi-arch OCI index.
FROM --platform=$BUILDPLATFORM node@sha256:9bef0ef1e268f60627da9ba7d7605e8831d5b56ad07487d24d1aa386336d1944 AS build

WORKDIR /app

ARG VITE_GOOGLE_CLIENT_ID=""
ARG VITE_API_URL=""
ARG VITE_ENABLE_BENCHMARKING=""
ARG VITE_MICROSOFT_AUTH_ENABLED=""
ARG VITE_MICROSOFT_SPA_CLIENT_ID=""
ARG VITE_MICROSOFT_AUTHORITY=""
ARG VITE_MICROSOFT_API_SCOPE=""
ARG VITE_MICROSOFT_REDIRECT_URI=""
ARG VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI=""
ARG VITE_GOOGLE_AUTH_ENABLED=""
ARG VITE_FEATURE_ENABLE_GITHUB_AUTH=""

ENV VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID
ENV VITE_API_URL=$VITE_API_URL
ENV VITE_ENABLE_BENCHMARKING=$VITE_ENABLE_BENCHMARKING
ENV VITE_MICROSOFT_AUTH_ENABLED=$VITE_MICROSOFT_AUTH_ENABLED
ENV VITE_MICROSOFT_SPA_CLIENT_ID=$VITE_MICROSOFT_SPA_CLIENT_ID
ENV VITE_MICROSOFT_AUTHORITY=$VITE_MICROSOFT_AUTHORITY
ENV VITE_MICROSOFT_API_SCOPE=$VITE_MICROSOFT_API_SCOPE
ENV VITE_MICROSOFT_REDIRECT_URI=$VITE_MICROSOFT_REDIRECT_URI
ENV VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI=$VITE_MICROSOFT_POST_LOGOUT_REDIRECT_URI
ENV VITE_GOOGLE_AUTH_ENABLED=$VITE_GOOGLE_AUTH_ENABLED
ENV VITE_FEATURE_ENABLE_GITHUB_AUTH=$VITE_FEATURE_ENABLE_GITHUB_AUTH

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Build Caddy v2.11.4 from its pinned upstream commit with the fixed Go/module
# dependency graph to satisfy vulnerability gates.
# Go 1.26.9 immutable multi-arch OCI index:
# sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade
FROM --platform=$BUILDPLATFORM golang@sha256:3082400e369fa24d5fc60bca20edab3f6d604e0c5a690ec66b295eff4dd87ade AS caddy-build

ARG CADDY_COMMIT=e2eee6a7fce366321294c9c2a79f3146891dcbdf
ARG CADDY_TAG=v2.11.4

RUN apk add --no-cache git

WORKDIR /src

RUN git init . && \
    git remote add origin https://github.com/caddyserver/caddy.git && \
    git fetch --depth 1 origin "refs/tags/${CADDY_TAG}:refs/tags/${CADDY_TAG}" && \
    test "$(git rev-list -n 1 "${CADDY_TAG}")" = "${CADDY_COMMIT}" && \
    git checkout --detach "${CADDY_TAG}"

# Fixed versions are selected from the Trivy fixed-version evidence for the
# embedded Caddy/Go dependency graph. The Go toolchain supplies the fixed stdlib.
RUN cp go.mod /tmp/caddy-patched.mod && \
    cp go.sum /tmp/caddy-patched.sum && \
    go get -modfile=/tmp/caddy-patched.mod \
        github.com/smallstep/certificates@v0.30.0 \
        google.golang.org/grpc@v1.83.2 \
        golang.org/x/crypto@v0.55.0 \
        golang.org/x/net@v0.60.0 \
        golang.org/x/text@v0.41.0 && \
    go mod tidy -modfile=/tmp/caddy-patched.mod && \
    go build -modfile=/tmp/caddy-patched.mod -trimpath -ldflags="-s -w" -o /out/caddy ./cmd/caddy

# caddy:2.11.4-alpine; immutable multi-arch OCI index.
# Registry manifest digest: sha256:de23def33b17fb5d1290b0f6c2add1d70780e52341896c00a4c8a2a2fe9d355e
FROM caddy@sha256:de23def33b17fb5d1290b0f6c2add1d70780e52341896c00a4c8a2a2fe9d355e AS production

COPY --from=caddy-build /out/caddy /usr/bin/caddy

WORKDIR /app

# Create non-root group and user appuser (UID 10001)
RUN addgroup -g 10001 -S appuser && \
    adduser -u 10001 -S appuser -G appuser

COPY deploy/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /usr/share/caddy/html

# Set appropriate ownership on /app and caddy runtime paths
RUN mkdir -p /data /config && \
    chown -R appuser:appuser /app /usr/share/caddy/html /etc/caddy/Caddyfile /data /config

USER appuser

EXPOSE 8080 8443

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
