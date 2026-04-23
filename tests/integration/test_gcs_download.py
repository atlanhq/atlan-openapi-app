"""Integration tests for the GCS cloud credential code path.

Validates that the connector correctly routes ``authType="gcs"`` credentials
through ``_create_gcs_store`` and runs the full Temporal-orchestrated
``import_type="CLOUD"`` workflow.

Why not fake-gcs-server?
    The upstream ``object_store`` Rust crate, when given a ``gcs_base_url``
    override, issues XML API-style requests (``PUT /{bucket}/{key}``) rather
    than JSON API paths (``POST /upload/storage/v1/b/{bucket}/o``).
    ``fake-gcs-server`` only handles JSON API routes, so object store I/O
    against it always returns 404.  No widely-available GCS emulator supports
    the XML API format that object_store generates.

    Instead, we monkeypatch ``_create_gcs_store`` to return an S3Store backed
    by MinIO.  This exercises the full connector workflow for a GCS credential
    shape without requiring a compatible GCS wire-protocol server.

Requires:
    - MinIO at ``AWS_ENDPOINT_URL`` (default: http://localhost:9000)
    - Temporal server at ``TEMPORAL_HOST`` (default: localhost:7233)

Run locally:
    docker run -d --rm -p 9000:9000 --name minio \\
        -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \\
        minio/minio server /data
    # create bucket
    AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \\
        aws --endpoint-url http://localhost:9000 \\
        s3api create-bucket --bucket test-openapi-specs --region us-east-1
    temporal server start-dev &
    AWS_ENDPOINT_URL=http://localhost:9000 \\
        uv run pytest tests/integration/test_gcs_download.py -v -m gcs_integration
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

import orjson
import pytest

from application_sdk.contracts.types import ConnectionRef
from application_sdk.storage.cloud import CloudStore

from app.connector import OpenAPIConnector
from app.contracts import OpenAPIConnectorInput, OpenAPIConnectorOutput

if TYPE_CHECKING:
    from tests.integration.conftest import AppExecutor

pytestmark = pytest.mark.gcs_integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MINIO_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:9000")
_MINIO_USER = os.environ.get("MINIO_ROOT_USER", "minioadmin")
_MINIO_PASS = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
_BUCKET = "test-openapi-specs"

# Synthetic GCS service account JSON — used as the credential password field
# to verify the connector accepts the gcs authType shape.  Not used for real
# GCS HTTP calls (the store is monkeypatched to MinIO in the workflow test).
_FAKE_SA_JSON = orjson.dumps(
    {
        "gcs_base_url": "http://unused",
        "disable_oauth": True,
        "client_email": "",
        "private_key": "",
        "private_key_id": "",
    }
).decode()

_PETSTORE_SPEC = {
    "openapi": "3.0.4",
    "info": {"title": "Petstore (GCS test)", "version": "1.0.0"},
    "paths": {
        "/pets": {"get": {"summary": "List pets"}},
        "/pets/{petId}": {
            "get": {"summary": "Get pet"},
            "delete": {"summary": "Delete pet"},
        },
    },
}

# Credential shape expected by CloudStore.from_credentials / _create_gcs_store
_GCS_CREDENTIAL: dict = {
    "authType": "gcs",
    "username": "",
    "password": _FAKE_SA_JSON,
    "extra": {"gcs_bucket": _BUCKET},
}

_FAKE_GUID = "test-fake-gcs-guid"
_CONNECTION_NAME = "test-openapi-cloud-gcs"
_CONNECTION_QN = f"default/api/{_CONNECTION_NAME}"


# ---------------------------------------------------------------------------
# Module-level MinIO check — skip the entire module if not reachable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def require_minio() -> None:
    """Skip all tests in this module if MinIO is not reachable."""
    import httpx

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{_MINIO_ENDPOINT}/minio/health/live")
            if resp.status_code >= 500:
                pytest.skip(f"MinIO not healthy at {_MINIO_ENDPOINT}")
    except Exception as exc:
        pytest.skip(f"MinIO not reachable at {_MINIO_ENDPOINT}: {exc}")


# ---------------------------------------------------------------------------
# Shared fixtures (module-scoped to seed the bucket once per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def seeded_bucket() -> str:
    """Seed the pre-created MinIO bucket with spec files."""
    from obstore.store import S3Store

    store = S3Store(
        bucket=_BUCKET,
        config={
            "aws_access_key_id": _MINIO_USER,
            "aws_secret_access_key": _MINIO_PASS,
            "aws_region": "us-east-1",
            "endpoint": _MINIO_ENDPOINT,
        },
        client_options={"allow_http": True},
    )
    cloud_store = CloudStore(store, provider="s3")

    spec_bytes = orjson.dumps(_PETSTORE_SPEC)
    for key in ("single/petstore.json", "multi/specA.json", "multi/specB.json"):
        await cloud_store.upload_bytes(key, spec_bytes)

    return _BUCKET


# ---------------------------------------------------------------------------
# TestGCSCloudDownloadWorkflow
# ---------------------------------------------------------------------------


class TestGCSCloudDownloadWorkflow:
    """Full Temporal-orchestrated workflow with import_type='CLOUD' for a GCS credential.

    Two monkeypatches are applied for the duration of the Temporal execution:

    * ``_create_gcs_store`` — substitutes an S3Store pointing at MinIO so that
      real object I/O works without a compatible GCS wire-protocol emulator.

    * ``CredentialResolver._resolve_by_guid`` — returns the test GCS credential
      dict without calling ``DaprCredentialVault``.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("cloud_gcs_workflow")

    @pytest.fixture(scope="class")
    async def gcs_extraction_result(
        self,
        openapi_executor: "AppExecutor",
        tmp_dir_class: Path,
        seeded_bucket: str,
    ) -> OpenAPIConnectorOutput:
        """Run the full CLOUD-import workflow using GCS credentials against MinIO."""
        import application_sdk.storage.cloud as cloud_mod
        from application_sdk.credentials.resolver import CredentialResolver
        from obstore.store import S3Store

        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        def _patched_create_gcs_store(creds: dict, extra: dict):
            bucket = extra.get("gcs_bucket", "")
            from application_sdk.storage.errors import StorageConfigError

            if not bucket:
                raise StorageConfigError("GCS bucket is required (extra.gcs_bucket)")
            return S3Store(
                bucket=bucket,
                config={
                    "aws_access_key_id": _MINIO_USER,
                    "aws_secret_access_key": _MINIO_PASS,
                    "aws_region": "us-east-1",
                    "endpoint": _MINIO_ENDPOINT,
                },
                client_options={"allow_http": True},
            )

        with (
            patch.object(cloud_mod, "_create_gcs_store", _patched_create_gcs_store),
            patch.object(
                CredentialResolver,
                "_resolve_by_guid",
                AsyncMock(return_value=_GCS_CREDENTIAL),
            ),
        ):
            result = cast(
                "OpenAPIConnectorOutput",
                await openapi_executor.execute_app(
                    OpenAPIConnector,
                    OpenAPIConnectorInput(
                        connection_usage="CREATE",
                        connection=ConnectionRef.model_validate(
                            {
                                "typeName": "Connection",
                                "attributes": {
                                    "qualifiedName": _CONNECTION_QN,
                                    "name": _CONNECTION_NAME,
                                    "category": "API",
                                    "adminGroups": ["admins"],
                                },
                            }
                        ),
                        import_type="CLOUD",
                        spec_prefix="single",
                        spec_key="petstore.json",
                        cloud_source=_FAKE_GUID,
                        output_dir=str(output_dir / "run1"),
                        load_to_atlan=False,
                    ),
                    execution_id_prefix="test-cloud-gcs",
                ),
            )

        return result

    async def test_workflow_completes(
        self, gcs_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert gcs_extraction_result is not None

    async def test_assets_extracted(
        self, gcs_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert gcs_extraction_result.api_spec_count >= 1
        assert gcs_extraction_result.api_path_count >= 1
        assert gcs_extraction_result.total_scanned >= 2

    async def test_no_atlan_loading(
        self, gcs_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert gcs_extraction_result.atlan_loaded_count == 0

    async def test_output_file_exists(
        self,
        gcs_extraction_result: OpenAPIConnectorOutput,
        store_root: Path,
    ) -> None:
        assert gcs_extraction_result.output_file is not None
        output_path = store_root / gcs_extraction_result.output_file.storage_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    async def test_output_contains_expected_types(
        self,
        gcs_extraction_result: OpenAPIConnectorOutput,
        store_root: Path,
    ) -> None:
        output_path = store_root / gcs_extraction_result.output_file.storage_path
        type_names: set[str] = set()
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    record = orjson.loads(line)
                    if "typeName" in record:
                        type_names.add(record["typeName"])
        assert "Connection" in type_names
        assert "APISpec" in type_names
        assert "APIPath" in type_names
