#!/usr/bin/env bash
# setup-services.sh — start MinIO and Azurite for integration tests.
#
# This script is invoked by the `services-script` hook in
# atlanhq/application-sdk/.github/actions/connector-integration-tests.
# It runs after `setup-deps` (which checks out this repo and installs deps)
# and before the app server starts, so source services are ready before any
# test that needs them.
#
# INTENTIONAL DIVERGENCE FROM FIXTURE-BASED PATTERN
# Most connectors should start source containers inside session-scoped pytest
# fixtures (see mysql/metabase conftest.py for the preferred approach).
# openapi keeps CI-level setup here deliberately: it exercises the
# `services-script` hook end-to-end so the pattern is proven by a real caller
# before any non-canonical connector needs it (review c_075b69).
#
# Environment variables are emitted via $GITHUB_ENV so they survive into the
# composite's later steps (connector-integration-tests runs in the same job).

set -euo pipefail

# ---------------------------------------------------------------------------
# Start MinIO (S3-compatible, port 9000) and Azurite (Azure Blob, port 10000)
# ---------------------------------------------------------------------------

docker run -d --name minio \
  -p 9000:9000 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data

docker run -d --name azurite \
  -p 10000:10000 \
  "mcr.microsoft.com/azure-storage/azurite:3.35.0@sha256:dae2a5f96553962901304b94e72ef87e299d0825e4b679673bcc527a25076fe4" \
  azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck

# Wait for both services to be ready (parallel health-checks).
until curl -sf http://localhost:9000/minio/health/live; do sleep 1; done &
until curl -s --max-time 2 http://127.0.0.1:10000/devstoreaccount1 > /dev/null 2>&1; do sleep 1; done &
wait

# ---------------------------------------------------------------------------
# Provision test buckets / containers
# ---------------------------------------------------------------------------

AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
  aws --endpoint-url http://localhost:9000 \
  s3api create-bucket --bucket test-openapi-specs --region us-east-1

az storage container create \
  --name test-openapi-specs \
  --connection-string "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"

# ---------------------------------------------------------------------------
# Export env vars so they survive into later steps in the same job.
# ---------------------------------------------------------------------------

{
  echo "AWS_ENDPOINT_URL=http://localhost:9000"
  echo "MINIO_ROOT_USER=minioadmin"
  echo "MINIO_ROOT_PASSWORD=minioadmin"
  echo "AZURE_STORAGE_ENDPOINT=http://127.0.0.1:10000"
} >> "$GITHUB_ENV"
