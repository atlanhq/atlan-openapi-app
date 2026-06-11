"""Integration tests for cloud object store download (Azure Blob via Azurite).

Tests two complementary layers:

1. ``TestAzureStoreDirectOperations`` — exercises the CloudStore API (upload,
   download, list) directly against Azurite without going through Temporal.
   Validates real HTTP I/O for the full SDK abstraction.

2. ``TestAzureCloudDownloadWorkflow`` — runs the full Temporal-orchestrated
   ``import_type="CLOUD"`` workflow, using monkeypatches to bypass Dapr
   credential resolution (GUID path) and redirect AzureStore to the local
   Azurite emulator.  The in-process worker means both patches are active
   for the full task execution.

Azurite ships with a well-known development account and key that are safe to
hard-code here — they are public and only valid against the emulator.

Requires:
    - Azurite blob service at ``AZURE_STORAGE_ENDPOINT`` (default: http://127.0.0.1:10000)
    - Temporal server at ``TEMPORAL_HOST`` (default: localhost:7233)

Run locally:
    docker run -d --rm -p 10000:10000 --name azurite \\
        mcr.microsoft.com/azure-storage/azurite:3.35.0 \\
        azurite-blob --blobHost 0.0.0.0
    # create container
    az storage container create --name test-openapi-specs \\
        --connection-string "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    temporal server start-dev &
    AZURE_STORAGE_ENDPOINT=http://127.0.0.1:10000 \\
        uv run pytest tests/integration/test_azure_download.py -v -m azure_integration
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

pytestmark = pytest.mark.azure_integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Azurite blob endpoint (e.g. http://127.0.0.1:10000)
_AZURITE_ENDPOINT = os.environ.get("AZURE_STORAGE_ENDPOINT", "http://127.0.0.1:10000")

# Azurite's well-known development credential (public, emulator-only)
_AZURITE_ACCOUNT = "devstoreaccount1"
_AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq"
    "/K1SZFPTOtr/KBHBeksoGMGw=="
)

_CONTAINER = "test-openapi-specs"

_PETSTORE_SPEC = {
    "openapi": "3.0.4",
    "info": {"title": "Petstore (Azure test)", "version": "1.0.0"},
    "paths": {
        "/pets": {"get": {"summary": "List pets"}},
        "/pets/{petId}": {
            "get": {"summary": "Get pet"},
            "delete": {"summary": "Delete pet"},
        },
    },
}

# Credential shape expected by CloudStore.from_credentials / _create_azure_store.
# username is the storage account name; password is the storage account key.
_AZURE_CREDENTIAL: dict = {
    "authType": "adls",
    "username": _AZURITE_ACCOUNT,
    "password": _AZURITE_KEY,
    "extra": {
        "storage_account_name": _AZURITE_ACCOUNT,
        "adls_container": _CONTAINER,
    },
}

_FAKE_GUID = "test-azurite-guid"
_CONNECTION_NAME = "test-openapi-cloud-azure"
_CONNECTION_QN = f"default/api/{_CONNECTION_NAME}"


# ---------------------------------------------------------------------------
# Module-level emulator check — skip the entire module if not reachable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def require_azurite() -> None:
    """Skip all tests in this module if Azurite is not reachable."""
    import httpx

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{_AZURITE_ENDPOINT}/{_AZURITE_ACCOUNT}")
            if resp.status_code >= 500:
                pytest.skip(f"Azurite not healthy at {_AZURITE_ENDPOINT}")
    except Exception as exc:
        pytest.skip(f"Azurite not reachable at {_AZURITE_ENDPOINT}: {exc}")


# ---------------------------------------------------------------------------
# Shared fixtures (module-scoped to seed the container once per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def seeded_container() -> str:
    """Seed the pre-created Azure blob container with spec files.

    Container creation is handled externally (CI: az storage container create;
    local dev: see module docstring).
    """
    from obstore.store import AzureStore

    store = AzureStore(
        container_name=_CONTAINER,
        config={
            "account_name": _AZURITE_ACCOUNT,
            "account_key": _AZURITE_KEY,
            "endpoint": f"{_AZURITE_ENDPOINT}/{_AZURITE_ACCOUNT}",
        },
        client_options={"allow_http": True},
    )
    cloud_store = CloudStore(store, provider="adls")

    spec_bytes = orjson.dumps(_PETSTORE_SPEC)
    for key in ("single/petstore.json", "multi/specA.json", "multi/specB.json"):
        await cloud_store.upload_bytes(key, spec_bytes)

    return _CONTAINER


# ---------------------------------------------------------------------------
# TestAzureStoreDirectOperations
# ---------------------------------------------------------------------------


class TestAzureStoreDirectOperations:
    """Exercises CloudStore methods directly against Azurite — no Temporal.

    Constructs AzureStore with the Azurite dev account key and the local
    emulator endpoint, bypassing ``from_credentials`` so that ``allow_http``
    and the custom endpoint are wired directly.
    """

    @pytest.fixture(scope="class")
    def cloud_store(self, seeded_container: str) -> CloudStore:
        from obstore.store import AzureStore

        store = AzureStore(
            container_name=seeded_container,
            config={
                "account_name": _AZURITE_ACCOUNT,
                "account_key": _AZURITE_KEY,
                "endpoint": f"{_AZURITE_ENDPOINT}/{_AZURITE_ACCOUNT}",
            },
            client_options={"allow_http": True},
        )
        return CloudStore(store, provider="adls")

    async def test_list_returns_seeded_keys(self, cloud_store: CloudStore) -> None:
        keys = await cloud_store.list(prefix="single")
        assert any("petstore.json" in k for k in keys), (
            f"petstore.json not found in {keys}"
        )

    async def test_get_bytes_single_key(self, cloud_store: CloudStore) -> None:
        data = await cloud_store.get_bytes("single/petstore.json")
        parsed = orjson.loads(data)
        assert parsed["info"]["title"] == "Petstore (Azure test)"
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
            "info": {"title": "Upload Test Azure", "version": "1.0"},
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
        assert parsed["info"]["title"] == "Upload Test Azure"

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
        ``result.stream``. This test wires those into the same Azurite store
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
        seeded_container: str,
        large_payload_file: tuple[Path, str, int],
    ) -> None:
        """A request timeout shorter than the transfer time fails with a timeout error.

        Wires obstore's ``client_options['timeout']`` to 1 ms (orders of
        magnitude below the time to ship 100 MiB even on loopback) and
        disables retries, then attempts to upload the 100 MiB fixture. The
        request must fail with a timeout-related ``StorageError``. This
        proves the timeout configuration is propagated through obstore to
        the underlying HTTP client and actually enforced.
        """
        from application_sdk.storage.errors import StorageError
        from obstore.store import AzureStore

        src_path, _, _ = large_payload_file

        timeout_store = AzureStore(
            container_name=seeded_container,
            config={
                "account_name": _AZURITE_ACCOUNT,
                "account_key": _AZURITE_KEY,
                "endpoint": f"{_AZURITE_ENDPOINT}/{_AZURITE_ACCOUNT}",
            },
            client_options={"allow_http": True, "timeout": "1ms"},
            retry_config={"max_retries": 0},
        )
        timeout_cloud = CloudStore(timeout_store, provider="adls")

        with pytest.raises(StorageError) as exc_info:
            await timeout_cloud.upload(
                local_path=src_path, key="timeout-test/upload.bin"
            )

        rendered = str(exc_info.value).lower()
        assert any(
            marker in rendered
            for marker in ("timeout", "timed out", "deadline", "elapsed")
        ), f"Expected a timeout-related error, got: {exc_info.value!r}"


# ---------------------------------------------------------------------------
# TestAzureCloudDownloadWorkflow
# ---------------------------------------------------------------------------


class TestAzureCloudDownloadWorkflow:
    """Full Temporal-orchestrated workflow with import_type='CLOUD' via Azurite.

    Two monkeypatches are applied for the duration of the Temporal execution:

    * ``_create_azure_store`` — builds an AzureStore pointing at Azurite by
      injecting the emulator endpoint and ``client_options={"allow_http": True}``.

    * ``CredentialResolver._resolve_by_guid`` — returns the test Azure credential
      dict without calling ``DaprCredentialVault``.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("cloud_azure_workflow")

    @pytest.fixture(scope="class")
    async def azure_extraction_result(
        self,
        openapi_executor: "AppExecutor",
        tmp_dir_class: Path,
        seeded_container: str,
    ) -> OpenAPIConnectorOutput:
        """Run the full CLOUD-import workflow against Azurite."""
        import application_sdk.storage.cloud as cloud_mod
        from application_sdk.credentials.resolver import CredentialResolver
        from obstore.store import AzureStore

        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        def _patched_create_azure_store(creds: dict, extra: dict):
            container = extra.get("adls_container", "objectstore")
            from application_sdk.storage.errors import StorageConfigError

            if not extra.get("storage_account_name"):
                raise StorageConfigError(
                    "Azure storage account is required (extra.storage_account_name)"
                )
            return AzureStore(
                container_name=container,
                config={
                    "account_name": _AZURITE_ACCOUNT,
                    "account_key": _AZURITE_KEY,
                    "endpoint": f"{_AZURITE_ENDPOINT}/{_AZURITE_ACCOUNT}",
                },
                client_options={"allow_http": True},
            ), None

        with (
            patch.object(cloud_mod, "_create_azure_store", _patched_create_azure_store),
            patch.object(
                CredentialResolver,
                "_resolve_by_guid",
                AsyncMock(return_value=_AZURE_CREDENTIAL),
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
                    execution_id_prefix="test-cloud-azure",
                ),
            )

        return result

    async def test_workflow_completes(
        self, azure_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert azure_extraction_result is not None

    async def test_assets_extracted(
        self, azure_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert azure_extraction_result.api_spec_count >= 1
        assert azure_extraction_result.api_path_count >= 1
        assert azure_extraction_result.total_scanned >= 2

    async def test_no_atlan_loading(
        self, azure_extraction_result: OpenAPIConnectorOutput
    ) -> None:
        assert azure_extraction_result.atlan_loaded_count == 0

    async def test_output_file_exists(
        self,
        azure_extraction_result: OpenAPIConnectorOutput,
        store_root: Path,
    ) -> None:
        assert azure_extraction_result.output_file is not None
        output_path = store_root / azure_extraction_result.output_file.storage_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    async def test_output_contains_expected_types(
        self,
        azure_extraction_result: OpenAPIConnectorOutput,
        store_root: Path,
    ) -> None:
        output_path = store_root / azure_extraction_result.output_file.storage_path
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
# TestAzureLargeSpecWorkflow
# ---------------------------------------------------------------------------


class TestAzureLargeSpecWorkflow:
    """Full Temporal-orchestrated workflow with a ≥100 MiB OpenAPI spec.

    Uploads the synthetic large spec into Azurite and runs the same
    ``import_type='CLOUD'`` workflow as ``TestAzureCloudDownloadWorkflow``,
    but at a payload size that exercises the connector's parser, extractor,
    and JSONL emit paths under load.
    """

    _LARGE_CONNECTION_NAME = "test-openapi-cloud-azure-large"
    _LARGE_CONNECTION_QN = f"default/api/{_LARGE_CONNECTION_NAME}"
    _LARGE_SPEC_KEY = "large/openapi.json"

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("cloud_azure_large_spec_workflow")

    @pytest_asyncio.fixture(scope="class")
    async def seeded_large_spec(
        self,
        seeded_container: str,  # ensures container exists
        large_spec_file: tuple[Path, str, int, int],
    ) -> tuple[str, int]:
        """Upload the large spec to a dedicated key. Returns ``(key, num_paths)``."""
        from obstore.store import AzureStore

        spec_path, _, _, num_paths = large_spec_file
        store = AzureStore(
            container_name=seeded_container,
            config={
                "account_name": _AZURITE_ACCOUNT,
                "account_key": _AZURITE_KEY,
                "endpoint": f"{_AZURITE_ENDPOINT}/{_AZURITE_ACCOUNT}",
            },
            client_options={"allow_http": True},
        )
        cloud_store = CloudStore(store, provider="adls")
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
        from obstore.store import AzureStore

        spec_key, num_paths = seeded_large_spec
        prefix, _, key = spec_key.rpartition("/")

        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        def _patched_create_azure_store(creds: dict, extra: dict):
            container = extra.get("adls_container", "objectstore")
            from application_sdk.storage.errors import StorageConfigError

            if not extra.get("storage_account_name"):
                raise StorageConfigError(
                    "Azure storage account is required (extra.storage_account_name)"
                )
            return AzureStore(
                container_name=container,
                config={
                    "account_name": _AZURITE_ACCOUNT,
                    "account_key": _AZURITE_KEY,
                    "endpoint": f"{_AZURITE_ENDPOINT}/{_AZURITE_ACCOUNT}",
                },
                client_options={"allow_http": True},
            ), None

        with (
            patch.object(cloud_mod, "_create_azure_store", _patched_create_azure_store),
            patch.object(
                CredentialResolver,
                "_resolve_by_guid",
                AsyncMock(return_value=_AZURE_CREDENTIAL),
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
                    execution_id_prefix="test-cloud-azure-large",
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
