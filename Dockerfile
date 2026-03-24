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

FROM registry.atlan.com/public/app-runtime-base:refactor-v3-latest

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
