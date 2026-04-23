"""Integration tests for cloud object store download (GCS via fake-gcs-server).

Tests two complementary layers:

1. ``TestGCSStoreDirectOperations`` — exercises the CloudStore API (upload,
   download, list) directly against fake-gcs-server without going through
   Temporal.  Validates real HTTP I/O for the full SDK abstraction.

2. ``TestGCSCloudDownloadWorkflow`` — runs the full Temporal-orchestrated
   ``import_type="CLOUD"`` workflow, using monkeypatches to bypass Dapr
   credential resolution (GUID path) and redirect GCSStore to the local
   emulator.

The emulator redirect works by passing a service account JSON that contains
``gcs_base_url`` and ``disable_oauth: true`` as documented by the upstream
Rust ``object_store`` crate.  The in-process worker means both patches are
active for the full task execution.

Requires:
    - fake-gcs-server at ``GCS_ENDPOINT_URL`` (default: http://localhost:4443)
    - Temporal server at ``TEMPORAL_HOST`` (default: localhost:7233)

Run locally:
    docker run -d --rm -p 4443:4443 --name fake-gcs-server \\
        fsouza/fake-gcs-server:1.54.0 -scheme http -port 4443
    # create bucket
    curl -X POST http://localhost:4443/storage/v1/b \\
        -H 'Content-Type: application/json' \\
        -d '{"name": "test-openapi-specs"}'
    temporal server start-dev &
    GCS_ENDPOINT_URL=http://localhost:4443 \\
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

_GCS_ENDPOINT = os.environ.get("GCS_ENDPOINT_URL", "http://localhost:4443")
_BUCKET = "test-openapi-specs"

# Service account JSON understood by object_store's GCS backend:
# gcs_base_url overrides the default storage.googleapis.com endpoint,
# and disable_oauth skips token fetching (safe for emulators).
_FAKE_SA_JSON = orjson.dumps(
    {
        "gcs_base_url": _GCS_ENDPOINT,
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
# Module-level emulator check — skip the entire module if not reachable
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def require_fake_gcs() -> None:
    """Skip all tests in this module if fake-gcs-server is not reachable."""
    import httpx

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{_GCS_ENDPOINT}/storage/v1/b")
            if resp.status_code >= 500:
                pytest.skip(f"fake-gcs-server not healthy at {_GCS_ENDPOINT}")
    except Exception as exc:
        pytest.skip(f"fake-gcs-server not reachable at {_GCS_ENDPOINT}: {exc}")


# ---------------------------------------------------------------------------
# Shared fixtures (module-scoped to seed the bucket once per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def seeded_bucket() -> str:
    """Seed the pre-created test bucket with spec files.

    Bucket creation is handled externally (CI: curl POST to GCS admin API;
    local dev: see module docstring).
    """
    from obstore.store import GCSStore

    store = GCSStore(
        bucket=_BUCKET,
        config={"service_account_key": _FAKE_SA_JSON},
        client_options={"allow_http": True},
    )
    cloud_store = CloudStore(store, provider="gcs")

    spec_bytes = orjson.dumps(_PETSTORE_SPEC)
    for key in ("single/petstore.json", "multi/specA.json", "multi/specB.json"):
        await cloud_store.upload_bytes(key, spec_bytes)

    return _BUCKET


# ---------------------------------------------------------------------------
# TestGCSStoreDirectOperations
# ---------------------------------------------------------------------------


class TestGCSStoreDirectOperations:
    """Exercises CloudStore methods directly against fake-gcs-server — no Temporal.

    Constructs GCSStore with a synthetic service account JSON that sets
    ``gcs_base_url`` to the local emulator and disables OAuth, per the
    object_store Rust crate's documented emulator support.
    """

    @pytest.fixture(scope="class")
    def cloud_store(self, seeded_bucket: str) -> CloudStore:
        from obstore.store import GCSStore

        store = GCSStore(
            bucket=seeded_bucket,
            config={"service_account_key": _FAKE_SA_JSON},
            client_options={"allow_http": True},
        )
        return CloudStore(store, provider="gcs")

    async def test_list_returns_seeded_keys(self, cloud_store: CloudStore) -> None:
        keys = await cloud_store.list(prefix="single")
        assert any("petstore.json" in k for k in keys), (
            f"petstore.json not found in {keys}"
        )

    async def test_get_bytes_single_key(self, cloud_store: CloudStore) -> None:
        data = await cloud_store.get_bytes("single/petstore.json")
        parsed = orjson.loads(data)
        assert parsed["info"]["title"] == "Petstore (GCS test)"
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
            "info": {"title": "Upload Test GCS", "version": "1.0"},
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
        assert parsed["info"]["title"] == "Upload Test GCS"


# ---------------------------------------------------------------------------
# TestGCSCloudDownloadWorkflow
# ---------------------------------------------------------------------------


class TestGCSCloudDownloadWorkflow:
    """Full Temporal-orchestrated workflow with import_type='CLOUD' via fake-gcs-server.

    Two monkeypatches are applied for the duration of the Temporal execution:

    * ``_create_gcs_store`` — builds a GCSStore pointing at the local emulator
      by injecting the synthetic service account JSON (gcs_base_url + disable_oauth)
      and ``client_options={"allow_http": True}``.

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
        """Run the full CLOUD-import workflow against fake-gcs-server."""
        import application_sdk.storage.cloud as cloud_mod
        from application_sdk.credentials.resolver import CredentialResolver
        from obstore.store import GCSStore

        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        def _patched_create_gcs_store(creds: dict, extra: dict):
            bucket = extra.get("gcs_bucket", "")
            from application_sdk.storage.errors import StorageConfigError

            if not bucket:
                raise StorageConfigError("GCS bucket is required (extra.gcs_bucket)")
            return GCSStore(
                bucket=bucket,
                config={"service_account_key": _FAKE_SA_JSON},
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
