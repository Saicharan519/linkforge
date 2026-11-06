# LinkForge frontend image (PLACEHOLDER, P1S.2). Serves one static page so the Kubernetes Ingress
# can route two hosts before the real app exists. In P4/P5 a Node build stage (npm ci && npm run
# build) is added in front of this and its output replaces ./public; this runtime stage stays.
#
# nginx-unprivileged: the official nginx image variant that runs as uid 101 and listens on 8080
# (ports below 1024 need root). Pinned by tag AND digest, same reasoning as the backend (D47).
FROM nginxinc/nginx-unprivileged:1.30-alpine-slim@sha256:5d8bb9437d74a600ec5dbf90c035451c724dc33eac8d3a71078f6f8612c345f6

# TEMPORARY security fix. The Trivy gate in CI (build-push.yml) started failing on 2026-11-06:
# CVE-2026-85091 (HIGH, heap overflow in zlib 1.3.2-r0, fixed in 1.3.2-r1). Our base image is
# pinned by digest, so it keeps the vulnerable zlib until upstream publishes a rebuilt image
# (checked: neither the 1.30 nor the newest alpine-slim tag had one yet). Until then we upgrade
# just that one package ourselves. Needs root for the moment; USER 101 below drops it again.
# REMOVE this block when a bumped base digest scans clean on its own (Dependabot, P6).
USER root
# 2026-11-07: the same thing happened again with musl (CVE-2026-107891, HIGH, heap/integer
# overflow in regcomp, installed 1.2.6-r2, fixed in 1.2.6-r3; musl-utils ships from the same
# source package). Same remedy, one line longer.
RUN apk upgrade --no-cache zlib musl musl-utils

# Replace the default server block with ours. Files are root-owned and read-only to uid 101.
COPY nginx.conf /etc/nginx/conf.d/default.conf
# The placeholder page moved from public/ to placeholder/ in P4.1 because Vite owns public/ and
# index.html now. The image content is unchanged until P4.7 swaps this for the real build output.
COPY placeholder /usr/share/nginx/html

# Already non-root in the base image; stated explicitly (numeric) so Kubernetes' runAsNonRoot can verify it.
USER 101

EXPOSE 8080

# Plain busybox wget exists in alpine. Kubernetes ignores this and uses its own probes.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
