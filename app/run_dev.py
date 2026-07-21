"""Dev server for the OpenAPI Spec Loader Connector.

This script runs a combined handler + worker for local development and testing.

The spec source is selected by ``import_type`` (a plain workflow field):

* ``import_type="URL"`` → fetch the public ``spec_url`` (no auth, no credential).
* ``import_type="CLOUD"`` → download the spec from an object store using the
  ``openapi_credential`` (an OBJECT-STORE credential: authType s3/gcs/adls with
  ``username`` / ``password`` / ``extra.*``) plus ``spec_prefix`` / ``spec_key``
  for the object location.

For the CLOUD path this dev script builds the object-store credential from
environment variables, stores it in an in-process ``MockSecretStore`` under the
name ``openapi_source``, and references it from ``example_input`` via a named
``CredentialRef``.

Usage:
    # 1. Start Temporal dev server (in a separate terminal):
    temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true

    # 2. Set environment variables:
    export OPENAPI_IMPORT_TYPE="URL"   # or "CLOUD"
    export OPENAPI_SPEC_URL="https://petstore3.swagger.io/api/v3/openapi.json"
    # For Atlan loading:
    # export ATLAN_API_KEY="atl-..."
    # export ATLAN_BASE_URL="https://your-tenant.atlan.com"

    # Object-store source instead of a URL (example, S3):
    # export OPENAPI_IMPORT_TYPE="CLOUD"
    # export OPENAPI_SOURCE_AUTH_TYPE="s3"
    # export OPENAPI_SOURCE_USERNAME="<access-key-id>"
    # export OPENAPI_SOURCE_PASSWORD="<secret-access-key>"
    # export OPENAPI_S3_BUCKET="my-bucket"
    # export OPENAPI_S3_REGION="us-east-1"
    # export OPENAPI_SPEC_PREFIX="specs"
    # export OPENAPI_SPEC_KEY="openapi.json"

    # 3. Start the dev server:
    python -m app.run_dev

    # 4. Extract metadata (public URL spec, no loading):
    curl -X POST http://localhost:8000/workflows/v1/start \\
      -H "Content-Type: application/json" \\
      -d '{
        "connection": {"qualifiedName": "default/api/test-openapi", "name": "test-openapi"},
        "connection_usage": "CREATE",
        "import_type": "URL",
        "spec_url": "https://petstore3.swagger.io/api/v3/openapi.json",
        "load_to_atlan": false
      }'
    # Response: {"success": true, "data": {"workflow_id": "...", "run_id": "..."}, ...}

    # 5. Check the result (replace <workflow_id> with value from response):
    curl http://localhost:8000/workflows/v1/result/<workflow_id>
"""

import asyncio
import orjson
import os
from typing import Any

from application_sdk.testing.mocks import MockSecretStore
from application_sdk.main import run_dev_combined

from app.connector import OpenAPIConnector

# Name the object-store source credential is stored under in the in-process
# secret store, referenced from example_input via a named CredentialRef (used
# only on the CLOUD path).
_SOURCE_CREDENTIAL_NAME = "openapi_source"


def _build_object_store_credential() -> dict[str, Any]:
    """Build the OBJECT-STORE credential dict from environment variables.

    Consumed by ``CloudStore.from_credentials`` in ``download_cloud_spec`` for
    the CLOUD import path. ``authType`` selects the provider (s3 / gcs / adls);
    auth material lives in ``username`` / ``password`` / ``extra``.
    """
    auth_type = os.environ.get("OPENAPI_SOURCE_AUTH_TYPE", "s3")

    extra: dict[str, str] = {}
    if auth_type == "s3":
        extra.update(
            {
                "s3_bucket": os.environ.get("OPENAPI_S3_BUCKET", ""),
                "region": os.environ.get("OPENAPI_S3_REGION", "us-east-1"),
                "aws_role_arn": os.environ.get("OPENAPI_AWS_ROLE_ARN", ""),
            }
        )
    elif auth_type == "gcs":
        extra.update({"gcs_bucket": os.environ.get("OPENAPI_GCS_BUCKET", "")})
    elif auth_type == "adls":
        extra.update(
            {
                "storage_account_name": os.environ.get(
                    "OPENAPI_AZURE_STORAGE_ACCOUNT", ""
                ),
                "adls_container": os.environ.get("OPENAPI_ADLS_CONTAINER", ""),
                "azure_tenant_id": os.environ.get("OPENAPI_AZURE_TENANT_ID", ""),
            }
        )

    return {
        "authType": auth_type,
        "username": os.environ.get("OPENAPI_SOURCE_USERNAME", ""),
        "password": os.environ.get("OPENAPI_SOURCE_PASSWORD", ""),
        "extra": extra,
    }


async def main() -> None:
    """Run the dev server."""
    import_type = os.environ.get("OPENAPI_IMPORT_TYPE", "URL")
    spec_url = os.environ.get(
        "OPENAPI_SPEC_URL", "https://petstore3.swagger.io/api/v3/openapi.json"
    )
    spec_prefix = os.environ.get("OPENAPI_SPEC_PREFIX", "")
    spec_key = os.environ.get("OPENAPI_SPEC_KEY", "")

    secrets: dict[str, str] = {}

    # Atlan credential for loading (optional)
    atlan_key = os.environ.get("ATLAN_API_KEY", "")
    if atlan_key:
        secrets["atlan"] = orjson.dumps(
            {
                "type": "atlan_api_token",
                "token": atlan_key,
                "base_url": os.environ.get("ATLAN_BASE_URL", ""),
            }
        ).decode()

    example_input: dict[str, Any] = {
        "connection": {
            "qualifiedName": "default/api/test-openapi",
            "name": "test-openapi",
        },
        "connection_usage": "CREATE",
        "import_type": import_type,
        "spec_url": spec_url,
        "spec_prefix": spec_prefix,
        "spec_key": spec_key,
        "load_to_atlan": False,
        "publish_dry_run": False,
    }

    # CLOUD path: build and reference the object-store credential. The URL path
    # fetches a public spec_url and never resolves a credential.
    if import_type == "CLOUD":
        secrets[_SOURCE_CREDENTIAL_NAME] = orjson.dumps(
            _build_object_store_credential()
        ).decode()
        example_input["openapi_credential"] = {
            "name": _SOURCE_CREDENTIAL_NAME,
            "credential_type": "openapi",
        }

    credential_stores = {"default": MockSecretStore(secrets)}

    await run_dev_combined(
        OpenAPIConnector,
        credential_stores=credential_stores,
        example_input=example_input,
    )


if __name__ == "__main__":
    asyncio.run(main())
