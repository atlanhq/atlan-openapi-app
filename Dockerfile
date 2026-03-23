# syntax=docker/dockerfile:1
# Dockerfile for openapi
#
# Extends the app-framework base image with the OpenAPI connector code and dependencies.
#
# Build args:
#   APP_MODULE - Python module path (app.connector:OpenAPIConnector)
#   BASE_IMAGE - App-framework base (default: ghcr.io/atlanhq/application-sdk:latest)
#
# Usage:
#   docker build \
#     --build-arg APP_MODULE=app.connector:OpenAPIConnector \
#     -t openapi:latest .

ARG BASE_IMAGE=ghcr.io/atlanhq/application-sdk:latest

# Stage 1: Install connector into the existing base venv
FROM ${BASE_IMAGE} AS builder

USER root
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# git is required for uv to fetch git-sourced dependencies (app-framework)
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files for dependency resolution
COPY pyproject.toml uv.lock ./
COPY app/ app/

# Install the connector package and its dependencies into the existing venv.
# Framework packages (app-framework) already present in base image
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

# TODO: remove the COPY and ENTRYPOINT below once the base image
# ghcr.io/atlanhq/application-sdk:latest is updated to include entrypoint.sh.
# The base image on refactor-v3 already has this; we're copying it here temporarily
# so the new Helm chart deployment pattern (--mode worker / --mode handler args) works now.
USER root
COPY --chown=appuser:appuser entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER appuser

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
