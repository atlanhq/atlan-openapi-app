"""Dev server for the OpenAPI Spec Loader Connector.

This script runs a combined handler + worker for local development and testing.

The spec source (URL vs object store) is selected entirely by the source
credential's ``authType``. Even a public URL is expressed as an
``authType="url"`` credential that carries ``spec_url`` (and no secret). This
dev script builds that credential from environment variables, stores it in an
in-process ``MockSecretStore`` under the name ``openapi_source``, and references
it from ``example_input`` via a named ``CredentialRef``.

Usage:
    # 1. Set environment variables:
    export OPENAPI_SPEC_URL="https://petstore3.swagger.io/api/v3/openapi.json"
    # For private specs with auth:
    # export OPENAPI_AUTH_HEADER="Bearer your-token"
    # For Atlan loading:
    # export ATLAN_API_KEY="atl-..."
    # export ATLAN_BASE_URL="https://your-tenant.atlan.com"

    # Object-store source instead of a URL (example, S3):
    # export OPENAPI_SOURCE_AUTH_TYPE="s3"
    # export OPENAPI_SOURCE_USERNAME="<access-key-id>"
    # export OPENAPI_SOURCE_PASSWORD="<secret-access-key>"
    # export OPENAPI_S3_BUCKET="my-bucket"
    # export OPENAPI_S3_REGION="us-east-1"
    # export OPENAPI_SPEC_PREFIX="specs"
    # export OPENAPI_SPEC_KEY="openapi.json"

    # 2. Start the dev server:
    python -m app.run_dev

    # 3. Start an extraction (public spec, no loading):
    curl -X POST http://localhost:8000/workflows/v1/start \\
      -H "Content-Type: application/json" \\
      -d '{
        "connection": {"qualifiedName": "default/api/test-openapi", "name": "test-openapi"},
        "connection_usage": "CREATE",
        "openapi_credential": {"name": "openapi_source", "credential_type": "openapi"},
        "load_to_atlan": false
      }'
    # Response: {"success": true, "data": {"workflow_id": "...", "run_id": "..."}, ...}

    # 4. Check the result (replace <workflow_id> with value from response):
    curl http://localhost:8000/workflows/v1/result/<workflow_id>
"""

import asyncio
import orjson
import os
from typing import Any

from application_sdk.testing.mocks import MockSecretStore
from application_sdk.main import run_dev_combined

from app.connector import OpenAPIConnector

# Name the source credential is stored under in the in-process secret store,
# referenced from example_input via a named CredentialRef.
_SOURCE_CREDENTIAL_NAME = "openapi_source"


def _build_source_credential() -> dict[str, Any]:
    """Build the source credential dict from environment variables.

    The ``authType`` selects the spec source consumed by the connector:
      * ``"url"``  → ``spec_url`` (+ optional ``auth_header``)
      * ``"s3"`` / ``"gcs"`` / ``"adls"`` → object-store auth in
        ``username`` / ``password`` / ``extra`` plus ``extra.spec_prefix`` /
        ``extra.spec_key`` (consumed by ``CloudStore.from_credentials`` and
        ``download_cloud_spec``).
    """
    auth_type = os.environ.get("OPENAPI_SOURCE_AUTH_TYPE", "url")

    if auth_type == "url":
        return {
            "authType": "url",
            "spec_url": os.environ.get(
                "OPENAPI_SPEC_URL",
                "https://petstore3.swagger.io/api/v3/openapi.json",
            ),
            "auth_header": os.environ.get("OPENAPI_AUTH_HEADER", ""),
        }

    # Object-store source (s3 / gcs / adls).
    extra: dict[str, str] = {
        "spec_prefix": os.environ.get("OPENAPI_SPEC_PREFIX", ""),
        "spec_key": os.environ.get("OPENAPI_SPEC_KEY", ""),
    }
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
    secrets: dict[str, str] = {
        _SOURCE_CREDENTIAL_NAME: orjson.dumps(_build_source_credential()).decode(),
    }

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

    credential_stores = {"default": MockSecretStore(secrets)}

    await run_dev_combined(
        OpenAPIConnector,
        credential_stores=credential_stores,
        example_input={
            "connection": {
                "qualifiedName": "default/api/test-openapi",
                "name": "test-openapi",
            },
            "connection_usage": "CREATE",
            # Named reference to the source credential stored above; the connector
            # resolves it to select the spec source via authType.
            "openapi_credential": {
                "name": _SOURCE_CREDENTIAL_NAME,
                "credential_type": "openapi",
            },
            "load_to_atlan": False,
            "publish_dry_run": False,
        },
    )


if __name__ == "__main__":
    asyncio.run(main())
