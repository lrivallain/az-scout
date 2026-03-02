ARG TARGETPLATFORM=linux/amd64

# ---------- build stage ----------
FROM --platform=${TARGETPLATFORM} python:3.13-slim AS builder

# Install build tools (git needed for hatch-vcs version)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy source and build metadata
COPY pyproject.toml README.md ./
COPY src/ src/
COPY .git/ .git/

# Build the wheel (hatch-vcs reads git tags for the version)
RUN pip install --no-cache-dir build hatchling hatch-vcs && \
    python -m build --wheel --outdir /build/dist

# ---------- runtime stage ----------
FROM --platform=${TARGETPLATFORM} python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/lrivallain/az-scout"
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
# In container deployments, mount a persistent volume at /app/data so that
# installed.json (plugin registry) and plugin-packages survive restarts.
# At startup the app reconciles: plugins listed in installed.json but missing
# from the packages directory are automatically reinstalled from their pinned
# commit SHA.
ENV AZ_SCOUT_DATA_DIR=/app/data
RUN mkdir -p /app/data && chown scout:scout /app/data
VOLUME /app/data

USER scout

EXPOSE 8000

# Container listens on 0.0.0.0:8000, no browser auto-open
ENTRYPOINT ["az-scout", "web", "--host", "0.0.0.0", "--port", "8000", "--no-open", "--proxy-headers"]
