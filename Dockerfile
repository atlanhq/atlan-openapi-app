# syntax=docker/dockerfile:1
# Dockerfile for openapi
#
# Extends the app-framework base image with the OpenAPI connector code and dependencies.
#
# Build args:
#   APP_MODULE - Python module path (app.connector:OpenAPIConnector); baked into ATLAN_APP_MODULE env var
#
# Usage:
#   docker build \
#     --build-arg APP_MODULE=app.connector:OpenAPIConnector \
#     -t openapi:latest .

FROM registry.atlan.com/public/app-runtime-base:refactor-v3-latest

ARG APP_MODULE=app.connector:OpenAPIConnector

# git is required for uv to fetch git-sourced dependencies (atlan-application-sdk)
USER root
RUN apk add --no-cache git
USER appuser

WORKDIR /app

# Copy lock files first for dependency caching
COPY --chown=appuser:appuser pyproject.toml uv.lock ./

# Install dependencies (excluding the project itself) into a new venv
RUN --mount=type=cache,target=/home/appuser/.cache/uv,uid=1000,gid=1000 \
    uv venv .venv && \
    uv sync --locked --no-install-project --no-dev

# Copy application code
COPY --chown=appuser:appuser app/ app/

ENV ATLAN_APP_MODULE=${APP_MODULE}
ENV ATLAN_CONTRACT_GENERATED_DIR=/app/app/generated
# Task queue is derived by the SDK from ATLAN_APPLICATION_NAME + ATLAN_DEPLOYMENT_NAME
# (set by Helm at runtime) → atlan-{app}-{deployment}, e.g. atlan-openapi-production.
# This matches the atlan-interim-apps template which produces the same pattern.
# Disable event interceptor — crossover 2.0 interim flow has no Dapr sidecar,
# so the eventstore binding is unavailable.
ENV APPLICATION_SDK_ENABLE_EVENT_INTERCEPTOR=false
