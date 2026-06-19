"""Integration tests for cloud object store download (S3 via MinIO).

Tests two complementary layers:

1. ``TestCloudStoreDirectOperations`` — exercises the CloudStore API (upload,
   download, list) directly against MinIO without going through Temporal.
   Validates real HTTP I/O for the full SDK abstraction.

2. ``TestS3CloudDownloadWorkflow`` — runs the full Temporal-orchestrated
   ``import_type="CLOUD"`` workflow, using monkeypatches to bypass Dapr
   credential resolution (GUID path) and inject ``allow_http`` so obstore
   accepts the HTTP MinIO endpoint.  The in-process worker means both patches
   are active for the full task execution.

Requires:
    - MinIO at ``AWS_ENDPOINT_URL`` (default: http://localhost:9000)
    - Temporal server at ``TEMPORAL_HOST`` (default: localhost:7233)

Run locally:
    docker run -d --rm -p 9000:9000 --name minio \\
        -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=minioadmin \\
        minio/minio server /data
    temporal server start-dev &
    AWS_ENDPOINT_URL=http://localhost:9000 \\
        uv run pytest tests/integration/test_cloud_download.py -v -m cloud_integration
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

import orjson
import pytest
import pytest_asyncio

from application_sdk.contracts.types import ConnectionRef
from application_sdk.storage.cloud import CloudStore

from app.connector import OpenAPIConnector
from app.contracts import OpenAPIConnectorInput, OpenAPIConnectorOutput

if TYPE_CHECKING:
    from tests.integration.conftest import AppExecutor

pytestmark = pytest.mark.cloud_integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MINIO_ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:9000")
_MINIO_USER = os.environ.get("MINIO_ROOT_USER", "minioadmin")
_MINIO_PASS = os.environ.get("MINIO_ROOT_PASSWORD", "minioadmin")
_BUCKET = "test-openapi-specs"

_PETSTORE_SPEC = {
    "openapi": "3.0.4",
    "info": {"title": "Petstore (MinIO test)", "version": "1.0.0"},
    "paths": {
        "/pets": {"get": {"summary": "List pets"}},
        "/pets/{petId}": {
            "get": {"summary": "Get pet"},
            "delete": {"summary": "Delete pet"},
        },
    },
}

# Shape expected by CloudStore.from_credentials / _create_s3_store
_S3_CREDENTIAL: dict = {
    "authType": "s3",
    "username": _MINIO_USER,
    "password": _MINIO_PASS,
    "extra": {"s3_bucket": _BUCKET, "region": "us-east-1"},
}

_FAKE_GUID = "test-minio-s3-guid"
_CONNECTION_NAME = "test-openapi-cloud-s3"
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
# Shared fixtures (module-scoped to create the bucket once per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def seeded_bucket() -> str:
    """Seed the pre-created test bucket with spec files.

    Bucket creation is handled externally (CI: aws s3api create-bucket;
    local dev: make test-cloud-integration creates it via AWS CLI).
    """
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
# TestCloudStoreDirectOperations
# ---------------------------------------------------------------------------


class TestS3CloudDownloadWorkflow:
    """Full Temporal-orchestrated workflow with import_type='CLOUD' via MinIO.

    Two monkeypatches are applied for the duration of the Temporal execution:

    * ``_create_s3_store`` — injects ``client_options={"allow_http": True}``
      so that obstore accepts the http:// MinIO endpoint (which the SDK does
      not expose via env vars through ``from_credentials``).

    * ``CredentialResolver._resolve_by_guid`` — returns the test S3 credential
      dict without calling ``DaprCredentialVault``, which is unavailable in the
      integration test environment.

    The in-process Temporal worker means both patches are visible to task code.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("cloud_s3_workflow")

    @pytest.fixture(scope="class")
    async def cloud_extraction_result(
        self,
        openapi_executor: "AppExecutor",
        tmp_dir_class: Path,
        seeded_bucket: str,  # ensures bucket exists before the workflow runs
    ) -> OpenAPIConnectorOutput:
        """Run the full CLOUD-import workflow against MinIO."""
        import application_sdk.storage.cloud as cloud_mod
        from application_sdk.credentials.resolver import CredentialResolver
        from obstore.store import S3Store

        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        def _patched_create_s3_store(creds: dict, extra: dict):
            bucket = extra.get("s3_bucket", "")
            from application_sdk.storage.errors import StorageConfigError

            if not bucket:
                raise StorageConfigError("S3 bucket is required (extra.s3_bucket)")
            config = {
                "aws_access_key_id": creds.get("username", ""),
                "aws_secret_access_key": creds.get("password", ""),
                "aws_region": extra.get("region", "us-east-1"),
                "endpoint": _MINIO_ENDPOINT,
            }
            return S3Store(
                bucket=bucket, config=config, client_options={"allow_http": True}
            ), None

        with (
            patch.object(cloud_mod, "_create_s3_store", _patched_create_s3_store),
            patch.object(
                CredentialResolver,
                "_resolve_by_guid",
                AsyncMock(return_value=_S3_CREDENTIAL),
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
                    execution_id_prefix="test-cloud-s3",
                ),
            )

        return result

    async def test_workflow_completes(
        self, cloud_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert cloud_extraction_result is not None

    async def test_assets_extracted(
        self, cloud_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert cloud_extraction_result.api_spec_count >= 1
        assert cloud_extraction_result.api_path_count >= 1
        assert cloud_extraction_result.total_scanned >= 2

    async def test_no_atlan_loading(
        self, cloud_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert cloud_extraction_result.atlan_loaded_count == 0

    async def test_output_file_exists(
        self,
        cloud_extraction_result: OpenAPIConnectorOutput,
        store_root: Path,
    ) -> None:
        assert cloud_extraction_result.output_file is not None
        output_path = store_root / cloud_extraction_result.output_file.storage_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    async def test_output_contains_expected_types(
        self,
        cloud_extraction_result: OpenAPIConnectorOutput,
        store_root: Path,
    ) -> None:
        output_path = store_root / cloud_extraction_result.output_file.storage_path
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


# ---------------------------------------------------------------------------
# TestS3LargeSpecWorkflow
# ---------------------------------------------------------------------------


class TestS3LargeSpecWorkflow:
    """Full Temporal-orchestrated workflow with a ≥100 MiB OpenAPI spec.

    Uploads the synthetic large spec into MinIO and runs the same
    ``import_type='CLOUD'`` workflow as ``TestS3CloudDownloadWorkflow``,
    but at a payload size that exercises the connector's parser, extractor,
    and JSONL emit paths under load. Catches regressions where small specs
    pass but a real customer-sized spec breaks (memory, timeout, parse).
    """

    _LARGE_CONNECTION_NAME = "test-openapi-cloud-s3-large"
    _LARGE_CONNECTION_QN = f"default/api/{_LARGE_CONNECTION_NAME}"
    _LARGE_SPEC_KEY = "large/openapi.json"

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("cloud_s3_large_spec_workflow")

    @pytest_asyncio.fixture(scope="class")
    async def seeded_large_spec(
        self,
        seeded_bucket: str,  # ensures bucket exists
        large_spec_file: tuple[Path, str, int, int],
    ) -> tuple[str, int]:
        """Upload the large spec to a dedicated key. Returns ``(key, num_paths)``."""
        from obstore.store import S3Store

        spec_path, _, _, num_paths = large_spec_file
        store = S3Store(
            bucket=seeded_bucket,
            config={
                "aws_access_key_id": _MINIO_USER,
                "aws_secret_access_key": _MINIO_PASS,
                "aws_region": "us-east-1",
                "endpoint": _MINIO_ENDPOINT,
            },
            client_options={"allow_http": True},
        )
        cloud_store = CloudStore(store, provider="s3")
        await cloud_store.upload(local_path=spec_path, key=self._LARGE_SPEC_KEY)
        return self._LARGE_SPEC_KEY, num_paths

    @pytest_asyncio.fixture(scope="class")
    async def large_extraction_result(
        self,
        openapi_executor: "AppExecutor",
        tmp_dir_class: Path,
        seeded_large_spec: tuple[str, int],
    ) -> tuple[OpenAPIConnectorOutput, int]:
        """Run the full CLOUD-import workflow against the large spec."""
        import application_sdk.storage.cloud as cloud_mod
        from application_sdk.credentials.resolver import CredentialResolver
        from obstore.store import S3Store

        spec_key, num_paths = seeded_large_spec
        prefix, _, key = spec_key.rpartition("/")

        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        def _patched_create_s3_store(creds: dict, extra: dict):
            bucket = extra.get("s3_bucket", "")
            from application_sdk.storage.errors import StorageConfigError

            if not bucket:
                raise StorageConfigError("S3 bucket is required (extra.s3_bucket)")
            config = {
                "aws_access_key_id": creds.get("username", ""),
                "aws_secret_access_key": creds.get("password", ""),
                "aws_region": extra.get("region", "us-east-1"),
                "endpoint": _MINIO_ENDPOINT,
            }
            return S3Store(
                bucket=bucket, config=config, client_options={"allow_http": True}
            ), None

        with (
            patch.object(cloud_mod, "_create_s3_store", _patched_create_s3_store),
            patch.object(
                CredentialResolver,
                "_resolve_by_guid",
                AsyncMock(return_value=_S3_CREDENTIAL),
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
                                    "qualifiedName": self._LARGE_CONNECTION_QN,
                                    "name": self._LARGE_CONNECTION_NAME,
                                    "category": "API",
                                    "adminGroups": ["admins"],
                                },
                            }
                        ),
                        import_type="CLOUD",
                        spec_prefix=prefix,
                        spec_key=key,
                        cloud_source=_FAKE_GUID,
                        output_dir=str(output_dir / "run1"),
                        load_to_atlan=False,
                    ),
                    execution_id_prefix="test-cloud-s3-large",
                ),
            )

        return result, num_paths

    async def test_workflow_completes(
        self,
        large_extraction_result: tuple[OpenAPIConnectorOutput, int],
    ) -> None:
        result, _ = large_extraction_result
        assert result is not None

    async def test_path_count_matches_input_spec(
        self,
        large_extraction_result: tuple[OpenAPIConnectorOutput, int],
    ) -> None:
        result, num_paths = large_extraction_result
        assert result.api_spec_count == 1
        # Connector emits exactly one OpenAPIPathRecord per path-url entry,
        # so the count must equal what we wrote into the synthetic spec.
        assert result.api_path_count == num_paths

    async def test_output_file_exists_and_non_empty(
        self,
        large_extraction_result: tuple[OpenAPIConnectorOutput, int],
        store_root: Path,
    ) -> None:
        result, _ = large_extraction_result
        assert result.output_file is not None
        output_path = store_root / result.output_file.storage_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0
