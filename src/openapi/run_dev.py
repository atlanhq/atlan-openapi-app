"""Dev server for the OpenAPI Spec Loader Connector.

This script runs a combined handler + worker for local development and testing.

Usage:
    # 1. Start Temporal dev server (in a separate terminal):
    temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true

    # 2. Set environment variables (see SPEC.md §10):
    export OPENAPI_SPEC_URL="https://petstore3.swagger.io/api/v3/openapi.json"
    # For private specs with auth:
    # export OPENAPI_AUTH_HEADER="Bearer your-token"
    # For Atlan loading:
    # export ATLAN_API_KEY="atl-..."
    # export ATLAN_BASE_URL="https://your-tenant.atlan.com"

    # 3. Start the dev server:
    python -m openapi.run_dev

    # 4. Extract metadata (public spec, no loading):
    curl -X POST http://localhost:8080/workflows/v1/start \\
      -H "Content-Type: application/json" \\
      -d '{
        "connection": {"qualifiedName": "default/api/test-openapi", "name": "test-openapi"},
        "spec_url": "https://petstore3.swagger.io/api/v3/openapi.json",
        "load_to_atlan": false
      }'
    # Response: {"success": true, "data": {"workflow_id": "...", "run_id": "..."}, ...}

    # 5. Check the result (replace <workflow_id> with value from response["data"]["workflow_id"]):
    curl http://localhost:8080/workflows/v1/result/<workflow_id>

    # 6. Full E2E with Atlan loading via publish-app (requires cluster with publish-app):
    curl -X POST http://localhost:8080/workflows/v1/start \\
      -H "Content-Type: application/json" \\
      -d '{
        "connection": {"qualifiedName": "default/api/test-openapi", "name": "test-openapi"},
        "spec_url": "https://petstore3.swagger.io/api/v3/openapi.json",
        "load_to_atlan": true
      }'
"""

import asyncio
import json
import os

# Import credential types to register them with the credential registry
import openapi.credentials  # noqa: F401 - registers 'openapi' credential type
from app_framework.main import run_dev_combined
from app_framework.infrastructure.secrets import InMemorySecretStore
from openapi.connector import OpenAPIConnector


async def main() -> None:
    """Run the dev server."""
    secrets: dict[str, str] = {}

    # Optional: OpenAPI credential for private spec endpoints
    auth_header = os.environ.get("OPENAPI_AUTH_HEADER", "")
    if auth_header:
        secrets["openapi"] = json.dumps(
            {
                "type": "openapi",
                "auth_header": auth_header,
            }
        )

    # Atlan credential for loading (optional)
    atlan_key = os.environ.get("ATLAN_API_KEY", "")
    if atlan_key:
        secrets["atlan"] = json.dumps(
            {
                "type": "atlan_api_token",
                "token": atlan_key,
                "base_url": os.environ.get("ATLAN_BASE_URL", ""),
            }
        )

    credential_stores = {"default": InMemorySecretStore(secrets)}

    spec_url = os.environ.get(
        "OPENAPI_SPEC_URL", "https://petstore3.swagger.io/api/v3/openapi.json"
    )

    await run_dev_combined(
        OpenAPIConnector,
        credential_stores=credential_stores,
        example_input={
            "connection": {
                "qualifiedName": "default/api/test-openapi",
                "name": "test-openapi",
            },
            "spec_url": spec_url,
            "load_to_atlan": False,
            "publish_dry_run": False,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
