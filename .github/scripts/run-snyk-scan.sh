#!/bin/bash
set -euo pipefail

# run-snyk-scan.sh - Execute Snyk container vulnerability scan
# Inputs: DOCKER_IMAGE, SNYK_TOKEN, SNYK_API (env vars)
# Outputs: snyk_results.json file

echo "Starting Snyk container scan..."

if [ -z "${DOCKER_IMAGE:-}" ]; then
    echo "Error: DOCKER_IMAGE environment variable is required"
    exit 1
fi

if [ -z "${SNYK_TOKEN:-}" ]; then
    echo "Error: SNYK_TOKEN environment variable is required"
    exit 1
fi

SNYK_API="${SNYK_API:-https://api.us.snyk.io}"

echo "Scanning image: $DOCKER_IMAGE"
echo "Using Snyk API: $SNYK_API"

snyk container test "$DOCKER_IMAGE" \
    --severity-threshold=high \
    --org=partner-images \
    --json-file-output=snyk_results.json || true

if [ ! -f snyk_results.json ]; then
    echo "Error: snyk_results.json was not created"
    exit 1
fi

echo "Snyk scan completed - results saved to snyk_results.json"
