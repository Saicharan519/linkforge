# LinkForge frontend image (PLACEHOLDER, P1S.2). Serves one static page so the Kubernetes Ingress
# can route two hosts before the real app exists. In P4/P5 a Node build stage (npm ci && npm run
# build) is added in front of this and its output replaces ./public; this runtime stage stays.
#
# nginx-unprivileged: the official nginx image variant that runs as uid 101 and listens on 8080
# (ports below 1024 need root). Pinned by tag AND digest, same reasoning as the backend (D47).
FROM nginxinc/nginx-unprivileged:1.30-alpine-slim@sha256:5d8bb9437d74a600ec5dbf90c035451c724dc33eac8d3a71078f6f8612c345f6

# Replace the default server block with ours. Files are root-owned and read-only to uid 101.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY public /usr/share/nginx/html

# Already non-root in the base image; stated explicitly (numeric) so Kubernetes' runAsNonRoot can verify it.
USER 101

EXPOSE 8080

# Plain busybox wget exists in alpine. Kubernetes ignores this and uses its own probes.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/healthz"]
