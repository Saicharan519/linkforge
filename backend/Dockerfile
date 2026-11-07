# LinkForge backend image. ONE image runs both the API and the migration job; only the command
# differs (API: uvicorn, default CMD. Migration: `alembic upgrade head`. Worker, later: `python -m app.worker`).
#
# What you should be able to explain after reading this file:
#   - a MULTI-STAGE build and why the final image is smaller and safer
#   - why we pin the base image by tag AND digest
#   - why the app runs as a non-root user
#   - why dependency installation comes BEFORE copying the source (layer caching)

# ---- Base image ---------------------------------------------------------------------------
# Pinned by exact tag (3.12.16, Debian 13 "trixie" slim) AND by content digest. A tag can be
# re-pointed to different bytes later; a digest cannot. Dependabot bumps both together (P6).
ARG PYTHON_IMAGE=python:3.12.16-slim-trixie@sha256:e695ab189fa2d082b1c61c69a6b888a5adeca61ceeccad107b1ed2a125721a9e

# ---- Stage 1: builder ---------------------------------------------------------------------
# Installs the Python dependencies into a virtual environment. Nothing from this stage except the
# finished venv reaches the final image, so pip, caches and any build tools stay behind.
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build

# A venv in a fixed path makes it trivial to copy as one directory into the next stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy ONLY the requirements file first. Docker caches each instruction as a layer and reuses it
# while its inputs are unchanged. Source code changes on nearly every commit, dependencies rarely:
# with this order, editing code does not re-download every package.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Stage 2: runtime ---------------------------------------------------------------------
# A fresh image from the same base. Only the venv and our code are copied in.
FROM ${PYTHON_IMAGE} AS runtime

# Run as an unprivileged user with a fixed numeric id. If the app is ever compromised, the
# attacker is not root inside the container. A NUMERIC id matters: Kubernetes' runAsNonRoot check
# can only verify numbers, not names. `--no-create-home` and `nologin`: this user cannot log in.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

# TEMPORARY (same pattern as the frontend image): the Debian base ships a zlib1g that Trivy flags until the
# base image is rebuilt upstream. We upgrade just that package ourselves (needs root; USER 10001 below drops it).
#  - 2026-11-07 zlib1g: CVE-2026-85091 and CVE-2026-76844 (HIGH, fixed in 1:1.3.dfsg+really1.3.1-1+deb13u1)
# REMOVE this block when a bumped base digest scans clean on its own (Dependabot).
RUN apt-get update \
 && apt-get install -y --no-install-recommends --only-upgrade zlib1g \
 && rm -rf /var/lib/apt/lists/*

ENV PATH="/opt/venv/bin:$PATH" \
    # Logs go straight to stdout/stderr (no buffering): `docker logs` and Kubernetes see them live.
    PYTHONUNBUFFERED=1 \
    # Do not write .pyc files: keeps the filesystem untouched, so it can be mounted read-only later.
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Nothing at run time installs packages, so the package installer must not be in the image: it is
# one more thing to patch (Trivy lists it) and a tool for anyone who got a shell. Two copies exist:
# the one inside the venv we just copied, and the one of the base image's own Python.
RUN python -m pip uninstall -y pip \
 && /usr/local/bin/python -m pip uninstall -y pip

# Application code and migrations. Tests and dev tools are excluded by .dockerignore.
# Root-owned and read-only to the app user: the running app cannot modify its own code.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .

USER 10001:10001

EXPOSE 8000

# Used by `docker compose` (depends_on: service_healthy) and `docker ps`. Kubernetes ignores this
# and uses its own probes. Plain Python, because the slim image has no curl or wget.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

# ONE uvicorn process per container (decision D15): we scale by running more containers/pods,
# and per-process metrics assume it. Exec form (a JSON list) so uvicorn is PID 1 and receives
# SIGTERM directly, which lets it finish in-flight requests when Kubernetes stops the pod.
#
# --no-proxy-headers: uvicorn would otherwise rewrite the client address from X-Forwarded-For for
# peers in its allow-list (default 127.0.0.1). The ONLY code allowed to interpret that header is
# app/core/client_ip.py (TRUSTED_PROXY_COUNT rule, ARCHITECTURE §6.3); no second, hidden rule.
#
# --no-access-log: uvicorn's access log prints the CLIENT ADDRESS of every request, which must
# never be logged. The app writes its own JSON access log without it (core/observability.py).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
