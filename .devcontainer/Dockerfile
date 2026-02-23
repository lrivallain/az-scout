# Dev container for az-scout
# Based on the official Python devcontainer image (Debian)
FROM mcr.microsoft.com/devcontainers/python:3.12

# Install extra system tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        jq \
        make \
        unzip \
        ripgrep \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
