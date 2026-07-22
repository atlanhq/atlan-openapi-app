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
        uv run pytest tests/integration/test_azure_download.py -v
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
from application_sdk.credentials.ref import CredentialRef
from application_sdk.storage.cloud import CloudStore

from app.connector import OpenAPIConnector
from app.contracts import OpenAPIConnectorInput, OpenAPIConnectorOutput

if TYPE_CHECKING:
    from tests.integration.conftest import AppExecutor

# Standard `integration` marker (fleet convention). Emulator availability is
# handled at runtime by the module-scoped `require_azurite` autouse fixture
# below, NOT by an addopts marker-deselection — the directory-scoped integration
# CI job runs `pytest tests/integration/` and a deselection would empty it.
pytestmark = pytest.mark.integration

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
# The object location comes from the workflow input's import_type / spec_prefix
# / spec_key; ``authType`` selects the source. ``download_cloud_spec`` resolves
# this credential via the GUID path.
def _azure_credential() -> dict:
    return {
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
                AsyncMock(return_value=_azure_credential()),
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
                        openapi_credential=CredentialRef(credential_guid=_FAKE_GUID),
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
        assert azure_extraction_result.publish_completed is False

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
                AsyncMock(return_value=_azure_credential()),
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
                        openapi_credential=CredentialRef(credential_guid=_FAKE_GUID),
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
