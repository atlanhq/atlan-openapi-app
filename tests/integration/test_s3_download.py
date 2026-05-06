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


class TestCloudStoreDirectOperations:
    """Exercises CloudStore methods directly against MinIO — no Temporal.

    Constructs CloudStore with explicit ``allow_http=True`` in ClientConfig,
    bypassing ``from_credentials`` so obstore's HTTP restriction is lifted.
    This validates the real HTTP I/O path (upload, download, list, get_bytes).
    """

    @pytest.fixture(scope="class")
    def cloud_store(self, seeded_bucket: str) -> CloudStore:
        from obstore.store import S3Store

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
        return CloudStore(store, provider="s3")

    async def test_list_returns_seeded_keys(self, cloud_store: CloudStore) -> None:
        keys = await cloud_store.list(prefix="single")
        assert any("petstore.json" in k for k in keys), (
            f"petstore.json not found in {keys}"
        )

    async def test_get_bytes_single_key(self, cloud_store: CloudStore) -> None:
        data = await cloud_store.get_bytes("single/petstore.json")
        parsed = orjson.loads(data)
        assert parsed["info"]["title"] == "Petstore (MinIO test)"
        assert len(parsed["paths"]) == 2

    async def test_download_single_key(
        self, cloud_store: CloudStore, tmp_path: Path
    ) -> None:
        paths = await cloud_store.download(
            key="single/petstore.json", output_dir=str(tmp_path / "single")
        )
        assert len(paths) == 1
        assert paths[0].exists()
        parsed = orjson.loads(paths[0].read_bytes())
        assert "paths" in parsed

    async def test_download_prefix(
        self, cloud_store: CloudStore, tmp_path: Path
    ) -> None:
        paths = await cloud_store.download(
            prefix="multi",
            output_dir=str(tmp_path / "multi"),
            suffix_filter={".json"},
        )
        assert len(paths) == 2
        for p in paths:
            assert p.suffix == ".json"
            assert p.exists()

    async def test_upload_then_download(
        self, cloud_store: CloudStore, tmp_path: Path
    ) -> None:
        upload_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Upload Test", "version": "1.0"},
            "paths": {"/ping": {"get": {"summary": "Ping"}}},
        }
        spec_bytes = orjson.dumps(upload_spec)

        uploaded = await cloud_store.upload_bytes("upload-test/spec.json", spec_bytes)
        assert uploaded == len(spec_bytes)

        paths = await cloud_store.download(
            key="upload-test/spec.json", output_dir=str(tmp_path / "upload")
        )
        assert len(paths) == 1
        parsed = orjson.loads(paths[0].read_bytes())
        assert parsed["info"]["title"] == "Upload Test"

    async def test_large_file_round_trip(
        self,
        cloud_store: CloudStore,
        large_payload_file: tuple[Path, str, int],
        tmp_path: Path,
    ) -> None:
        """Round-trip a ≥100 MiB payload through the production CloudStore path.

        Mirrors what ``download_cloud_spec`` does in production for customer
        OpenAPI specs — same ``CloudStore.upload`` / ``CloudStore.download``
        calls, just with a 100 MiB+ payload instead of a few-hundred-byte
        JSON. SHA-256 is checked end-to-end so a single corrupted byte fails
        the test.
        """
        from tests.integration.conftest import sha256_of_path

        src_path, src_sha256, src_size = large_payload_file
        key = "large/payload.bin"

        uploaded = await cloud_store.upload(local_path=src_path, key=key)
        assert uploaded == src_size

        paths = await cloud_store.download(key=key, output_dir=str(tmp_path / "large"))
        assert len(paths) == 1
        dl_path = paths[0]
        assert dl_path.stat().st_size == src_size
        assert sha256_of_path(dl_path) == src_sha256

    async def test_large_file_round_trip_via_sdk_chunking_apis(
        self,
        cloud_store: CloudStore,
        large_payload_file: tuple[Path, str, int],
        tmp_path: Path,
    ) -> None:
        """Round-trip a ≥100 MiB payload through the SDK's chunking entry points.

        ``CloudStore.upload`` / ``download`` use single PUT/GET. The SDK's
        ``storage.ops.upload_file`` uses ``obstore.open_writer_async`` which
        does multipart upload, and ``download_file`` streams the GET via
        ``result.stream``. This test wires those into the same MinIO store
        so the multipart-upload and streaming-download paths actually run
        on a 100 MiB+ payload.
        """
        from application_sdk.storage.ops import download_file, upload_file

        src_path, src_sha256, src_size = large_payload_file
        key = "large-chunked/payload.bin"

        digest = await upload_file(
            key=key,
            local_path=src_path,
            store=cloud_store.store,
            normalize=False,
        )
        assert digest == src_sha256

        dl_path = tmp_path / "chunked" / "payload.bin"
        downloaded_digest = await download_file(
            key=key,
            local_path=dl_path,
            store=cloud_store.store,
            compute_hash=True,
            normalize=False,
        )
        assert dl_path.stat().st_size == src_size
        assert downloaded_digest == src_sha256

    async def test_upload_respects_short_request_timeout(
        self,
        seeded_bucket: str,
        large_payload_file: tuple[Path, str, int],
    ) -> None:
        """A request timeout shorter than the transfer time fails with a timeout error.

        Wires obstore's ``client_options['timeout']`` to 100 ms, then attempts
        to upload the 100 MiB fixture — the transfer is guaranteed to exceed
        100 ms even on loopback, so this proves the timeout configuration is
        propagated through obstore to the underlying HTTP client and actually
        enforced. A flaky network can't pass this test by accident: failing
        means the SDK is not honoring the configured deadline.
        """
        from application_sdk.storage.errors import StorageError
        from obstore.store import S3Store

        src_path, _, _ = large_payload_file

        timeout_store = S3Store(
            bucket=seeded_bucket,
            config={
                "aws_access_key_id": _MINIO_USER,
                "aws_secret_access_key": _MINIO_PASS,
                "aws_region": "us-east-1",
                "endpoint": _MINIO_ENDPOINT,
            },
            client_options={"allow_http": True, "timeout": "100ms"},
        )
        timeout_cloud = CloudStore(timeout_store, provider="s3")

        with pytest.raises(StorageError) as exc_info:
            await timeout_cloud.upload(
                local_path=src_path, key="timeout-test/upload.bin"
            )

        # StorageError.__str__ embeds the wrapped cause's class + message.
        rendered = str(exc_info.value).lower()
        assert any(
            marker in rendered for marker in ("timeout", "timed out", "deadline")
        ), f"Expected a timeout-related error, got: {exc_info.value!r}"


# ---------------------------------------------------------------------------
# TestS3CloudDownloadWorkflow
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
            )

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
