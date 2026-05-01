# ---------- build stage ----------
FROM python:3.13-slim AS builder

WORKDIR /build

# Version is injected by CI (computed from the git tag / branch SHA on the
# host) instead of being derived from `.git/` inside the container.  Doing the
# latter caused issue #159: the selective `COPY` below makes git see most of
# the worktree as deleted ("dirty"), which made setuptools-scm fall back to
# the next-dev version (e.g. tag v2026.4.1 was reported as 2026.4.2.dev0 in
# the running container).  Pretending the version makes the build bit-for-bit
# deterministic, drops the `git` apt dependency from the builder stage, and
# avoids shipping `.git/` into the build context.
ARG AZ_SCOUT_VERSION
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${AZ_SCOUT_VERSION}

# Copy source and build metadata
COPY pyproject.toml README.md ./
COPY src/ src/

# Build the wheel
RUN pip install --no-cache-dir build hatchling hatch-vcs && \
    python -m build --wheel --outdir /build/dist

# ---------- runtime stage ----------
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/az-scout/az-scout"
LABEL org.opencontainers.image.description="Azure Scout — explore availability zones, capacity, pricing, and plan VM deployments"
LABEL org.opencontainers.image.licenses="MIT"

# Git is needed at runtime so the plugin manager can install from git URLs
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r scout && useradd -r -g scout -d /app scout
WORKDIR /app

# Install the wheel from the build stage
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Install uv so the plugin manager can install packages quickly
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Plugin data directory.
# Mount a persistent volume at /app/data so that installed.json (plugin
# registry) and audit.jsonl survive restarts.  Plugin packages and the uv
# cache live on the local filesystem (/tmp) because Azure Files (SMB) does
# not support chmod/hardlinks.  On restart the reconcile loop reinstalls
# every plugin from its pinned commit SHA recorded in installed.json.
ENV AZ_SCOUT_DATA_DIR=/app/data
ENV AZ_SCOUT_PACKAGES_DIR=/tmp/az-scout-packages
RUN mkdir -p /app/data && chown scout:scout /app/data
VOLUME /app/data

USER scout

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"]

# Container listens on 0.0.0.0:8000, no browser auto-open
ENTRYPOINT ["az-scout", "web", "--host", "0.0.0.0", "--port", "8000", "--no-open", "--proxy-headers"]
