# syntax=docker/dockerfile:1
# Dockerfile for openapi
#
# Extends the app-framework base image with the OpenAPI connector code and dependencies.
#
# Build args:
#   APP_MODULE - Python module path (openapi.connector:OpenAPIConnector)
#   BASE_IMAGE - App-framework base (default: ghcr.io/atlanhq/experimental-app-sdk:latest)
#
# Usage:
#   docker build \
#     --build-arg APP_MODULE=openapi.connector:OpenAPIConnector \
#     -t openapi:latest .

ARG BASE_IMAGE=ghcr.io/atlanhq/experimental-app-sdk:latest

# Stage 1: Install connector into the existing base venv
FROM ${BASE_IMAGE} AS builder

USER root
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# git is required for uv to fetch git-sourced dependencies (app-framework, atlan-loader)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files for dependency resolution
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Install the connector package and its dependencies into the existing venv.
# Framework packages (app-framework, atlan-loader) already present in base image
# are reused — only new connector-specific dependencies are added.
RUN --mount=type=secret,id=GIT_AUTH_TOKEN \
    git config --global url."https://$(cat /run/secrets/GIT_AUTH_TOKEN)@github.com/".insteadOf "https://github.com/" && \
    uv sync --frozen --no-dev --no-editable

# Stage 2: Final image — copy only new/updated packages from builder
FROM ${BASE_IMAGE}

ARG APP_MODULE

USER root
COPY --from=builder --chown=appuser:appuser /app/.venv/lib /app/.venv/lib
USER appuser

ENV ATLAN_APP_MODULE_DEFAULT=${APP_MODULE}
