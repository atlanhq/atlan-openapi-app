# syntax=docker/dockerfile:1
# Dockerfile for openapi
#
# Extends the app-framework base image with the OpenAPI connector code and dependencies.
#
# Usage:
#   docker build -t openapi:latest .

FROM registry.atlan.com/public/app-runtime-base:3

WORKDIR /app

# Copy lock files first for dependency caching
COPY --chown=appuser:appuser pyproject.toml uv.lock ./

# Install dependencies (excluding the project itself) into a new venv
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000,gid=1000 \
    uv venv .venv && \
    uv sync --locked --no-install-project --no-dev

# Copy application code
COPY --chown=appuser:appuser app/ app/

ENV ATLAN_APP_MODULE=app.connector:OpenAPIConnector
ENV ATLAN_CONTRACT_GENERATED_DIR=/app/app/generated
# Task queue is derived by the SDK from ATLAN_APPLICATION_NAME + ATLAN_DEPLOYMENT_NAME
# (set by Helm at runtime) → atlan-{app}-{deployment}, e.g. atlan-openapi-production.
# Disable event interceptor — the Dapr eventstore binding is unavailable at runtime.
ENV APPLICATION_SDK_ENABLE_EVENT_INTERCEPTOR=false
