# LinkForge frontend image (P5.2): the built React app served by nginx.
#
# Two stages. The first one has Node and every build tool; it produces static files (HTML, JS,
# CSS, fonts) in /app/dist. The second one is a plain web server that receives ONLY those files.
# Node, npm, node_modules and the source code never reach the image that runs, which keeps it
# small and leaves nothing for an attacker to use.

# ---- Stage 1: build --------------------------------------------------------------------------
# Pinned by tag AND digest (the tag says what we meant, the digest is what we actually get; a tag
# can be moved, a digest cannot). Node 22 LTS is the version the project is developed and tested on.
FROM node:22-alpine@sha256:0e1754758c2df4cfa3e68d59e2e1b7374bb7fd9ea6328eaaacf15cc934af8202 AS build
WORKDIR /app

# Dependencies first, alone: Docker caches this layer, so editing a source file does not
# reinstall everything. `npm ci` installs exactly what package-lock.json says and fails if the
# lock file and package.json disagree.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Then the source, then the production build (type check + Vite bundle, see package.json).
# .dockerignore keeps node_modules, dist, env files and docs out of this COPY.
COPY . .
RUN npm run build

# ---- Stage 2: runtime ------------------------------------------------------------------------
# nginx-unprivileged: the official nginx image variant that runs as uid 101 and listens on 8080
# (ports below 1024 need root). Pinned by tag AND digest, same reasoning as the backend (D47).
FROM nginxinc/nginx-unprivileged:1.30-alpine-slim@sha256:5d8bb9437d74a600ec5dbf90c035451c724dc33eac8d3a71078f6f8612c345f6

# TEMPORARY security fixes. The Trivy gate in CI (build-push.yml) fails the build when a fixable
# HIGH/CRITICAL vulnerability is in the image. Our base image is pinned by digest, so it keeps
# vulnerable packages until upstream publishes a rebuilt image. Until then we upgrade just those
# packages ourselves (needs root; USER 101 below drops it again):
#  - 2026-11-06 zlib: CVE-2026-85091 (HIGH, heap overflow, fixed in 1.3.2-r1)
#  - 2026-11-07 musl, musl-utils: CVE-2026-107891 (HIGH, integer overflow in regcomp, fixed in 1.2.6-r3)
# REMOVE this block when a bumped base digest scans clean on its own (Dependabot, P6).
USER root
RUN apk upgrade --no-cache zlib musl musl-utils

# Replace the default server block with ours, add the shared security-header snippet, and copy
# the built app. Everything is root-owned and read-only to uid 101.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html

# Already non-root in the base image; stated explicitly (numeric) so Kubernetes' runAsNonRoot can verify it.
USER 101

EXPOSE 8080

# Plain busybox wget exists in alpine. Kubernetes ignores this and uses its own probes.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
