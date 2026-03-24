# syntax=docker/dockerfile:1
# Dockerfile for openapi
#
# Extends the app-framework base image with the OpenAPI connector code and dependencies.
#
# Build args:
#   APP_MODULE - Python module path (app.connector:OpenAPIConnector)
#
# Usage:
#   docker build \
#     --build-arg APP_MODULE=app.connector:OpenAPIConnector \
#     -t openapi:latest .

FROM registry.atlan.com/public/application-sdk:main-latest

ARG APP_MODULE

# git is required for uv to fetch git-sourced dependencies (atlan-application-sdk)
USER root
RUN apk add --no-cache git

WORKDIR /app

# Copy lock files first for dependency caching
COPY --chown=appuser:appuser pyproject.toml uv.lock ./

# Install dependencies (excluding the project itself) into a new venv
RUN uv venv .venv && uv sync --locked --no-install-project --no-dev

# Copy application code
COPY --chown=appuser:appuser app/ app/

USER appuser

ENV ATLAN_APP_MODULE_DEFAULT=${APP_MODULE}

# TODO: remove the COPY and ENTRYPOINT below once the base image
# registry.atlan.com/public/application-sdk:main-latest is updated to include entrypoint.sh.
# The base image on refactor-v3 already has this; we're copying it here temporarily
# so the new Helm chart deployment pattern (--mode worker / --mode handler args) works now.
USER root
COPY --chown=appuser:appuser entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
USER appuser

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
