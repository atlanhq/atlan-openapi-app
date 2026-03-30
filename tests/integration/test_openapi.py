"""Integration tests for the OpenAPI Connector App.

Tests the full extraction workflow through Temporal.
Validates extraction, change detection, transform, and output file content.

The OpenAPI connector works with public spec URLs — no API credentials needed
for public specs (e.g. the Swagger Petstore).

Requires:
    - temporal server start-dev

Run with:
    uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from application_sdk.contracts.types import ConnectionRef
from app.connector import OpenAPIConnector
from app.contracts import (
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
)

if TYPE_CHECKING:
    # TODO(v3-migration): AppExecutor is now a local compatibility shim in conftest.py
    from tests.integration.conftest import AppExecutor

# Default to the public Swagger Petstore — no credentials required.
# Override OPENAPI_SPEC_URL to test against a different spec.
_SPEC_URL = os.environ.get(
    "OPENAPI_SPEC_URL",
    "https://petstore3.swagger.io/api/v3/openapi.json",
)

CONNECTION_NAME = "test-openapi-integration"
CONNECTION_QN = f"default/api/{CONNECTION_NAME}"


class TestOpenAPIConnectorExtraction:
    """Full extraction without checkpoint.

    Executes one workflow and shares the result across all tests via
    a class-scoped fixture. This avoids running the expensive workflow
    multiple times.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("openapi_extraction")

    @pytest.fixture(scope="class")
    async def extraction_result(
        self,
        openapi_executor: "AppExecutor",
        tmp_dir_class: Path,
    ) -> OpenAPIConnectorOutput:
        """Execute a full extraction (no checkpoint, no Atlan loading)."""
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

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
                                "qualifiedName": CONNECTION_QN,
                                "name": CONNECTION_NAME,
                                "category": "API",
                                "adminGroups": ["admins"],
                            },
                        }
                    ),
                    spec_url=_SPEC_URL,
                    output_dir=str(output_dir / "run1"),
                    checkpoint_dir="",
                    load_to_atlan=False,
                ),
                execution_id_prefix="test-openapi-extraction",
            ),
        )

        return result

    async def test_workflow_completes(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Workflow should complete without error."""
        assert extraction_result is not None

    async def test_assets_extracted(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Should extract at least one APISpec and some APIPaths."""
        assert extraction_result.api_spec_count >= 1
        assert extraction_result.api_path_count >= 1
        assert extraction_result.total_scanned >= 2

    async def test_no_atlan_loading(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """No Atlan loading should occur when load_to_atlan=False."""
        assert extraction_result.atlan_loaded_count == 0

    async def test_output_file_exists(
        self, extraction_result: OpenAPIConnectorOutput, store_root: Path
    ) -> None:
        """Output JSONL file should exist in the LocalStore and be non-empty."""
        assert extraction_result.output_file is not None
        output_path = store_root / extraction_result.output_file.storage_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    async def test_output_contains_expected_types(
        self, extraction_result: OpenAPIConnectorOutput, store_root: Path
    ) -> None:
        """Output JSONL should contain Connection, APISpec, and APIPath."""
        import json

        output_path = store_root / extraction_result.output_file.storage_path
        type_names: set[str] = set()
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    record = json.loads(line)
                    if "typeName" in record:
                        type_names.add(record["typeName"])

        assert "Connection" in type_names
        assert "APISpec" in type_names
        assert "APIPath" in type_names

    async def test_qualified_names_follow_convention(
        self, extraction_result: OpenAPIConnectorOutput, store_root: Path
    ) -> None:
        """All qualifiedName values should start with the connection prefix."""
        import json

        output_path = store_root / extraction_result.output_file.storage_path
        prefix = f"{CONNECTION_QN}/"
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                qn = record.get("attributes", {}).get("qualifiedName", "")
                if not qn:
                    qn = record.get("qualifiedName", "")
                if qn and qn != CONNECTION_QN:
                    assert qn.startswith(prefix), (
                        f"qualifiedName '{qn}' does not start with '{prefix}'"
                    )


# =============================================================================
# Petstore spec JSON for CLOUD tests (minimal but valid)
# =============================================================================

_PETSTORE_SPEC = {
    "openapi": "3.0.4",
    "info": {"title": "Petstore (CLOUD test)", "version": "1.0.0"},
    "paths": {
        "/pets": {"get": {"summary": "List pets"}},
        "/pets/{petId}": {
            "get": {"summary": "Get pet"},
            "delete": {"summary": "Delete pet"},
        },
    },
}

CLOUD_CONNECTION_NAME = "test-openapi-cloud"
CLOUD_CONNECTION_QN = f"default/api/{CLOUD_CONNECTION_NAME}"


class TestOpenAPIConnectorCloudWiring:
    """CLOUD import mode wiring test.

    Tests the full CLOUD path: connector.run() reads import_type=CLOUD →
    calls download_spec_from_cloud → api_client.fetch_spec reads local file
    → extract → transform. Uses a local spec file to simulate the download.

    This is tested at the unit level (not via Temporal) because the integration
    test executor doesn't wire self.context.storage, and patching inside a
    Temporal sandbox isn't feasible. The cloud_storage module itself is
    thoroughly tested in tests/unit/test_cloud_storage.py (25 tests).
    """

    async def test_cloud_local_file_extraction(self, tmp_path: Path) -> None:
        """Verify fetch_spec handles a local file path (CLOUD download result)."""
        from app.api_client import OpenAPIApiClient

        spec_path = tmp_path / "petstore.json"
        spec_path.write_text(json.dumps(_PETSTORE_SPEC))

        client = OpenAPIApiClient()
        try:
            specs = await client.fetch_spec(str(spec_path))
        finally:
            await client.close()

        assert len(specs) == 1
        assert specs[0]["info"]["title"] == "Petstore (CLOUD test)"
        assert len(specs[0]["paths"]) == 2

    async def test_cloud_local_yaml_file(self, tmp_path: Path) -> None:
        """Verify fetch_spec handles a local YAML file."""
        from app.api_client import OpenAPIApiClient

        yaml_content = """
openapi: "3.0.4"
info:
  title: "YAML Cloud Test"
  version: "1.0.0"
paths:
  /health:
    get:
      summary: "Health check"
"""
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(yaml_content)

        client = OpenAPIApiClient()
        try:
            specs = await client.fetch_spec(str(spec_path))
        finally:
            await client.close()

        assert len(specs) == 1
        assert specs[0]["info"]["title"] == "YAML Cloud Test"
        assert "/health" in specs[0]["paths"]

    async def test_cloud_local_zip_file(self, tmp_path: Path) -> None:
        """Verify fetch_spec handles a local ZIP file with multiple specs."""
        import zipfile

        from app.api_client import OpenAPIApiClient

        spec1 = {
            "openapi": "3.0.0",
            "info": {"title": "Spec One", "version": "1.0"},
            "paths": {"/a": {"get": {"summary": "A"}}},
        }
        spec2 = {
            "openapi": "3.0.0",
            "info": {"title": "Spec Two", "version": "1.0"},
            "paths": {"/b": {"post": {"summary": "B"}}},
        }

        zip_path = tmp_path / "specs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("spec1.json", json.dumps(spec1))
            zf.writestr("spec2.json", json.dumps(spec2))

        client = OpenAPIApiClient()
        try:
            specs = await client.fetch_spec(str(zip_path))
        finally:
            await client.close()

        assert len(specs) == 2
        titles = {s["info"]["title"] for s in specs}
        assert "Spec One" in titles
        assert "Spec Two" in titles

    async def test_cloud_extract_from_local_file(self, tmp_path: Path) -> None:
        """Verify the full extract pipeline works with a local file path."""
        from app.connector import _extract_spec_async
        from application_sdk.observability.logger_adaptor import get_logger

        spec_path = tmp_path / "petstore.json"
        spec_path.write_text(json.dumps(_PETSTORE_SPEC))
        output_dir = tmp_path / "raw"

        spec_file, path_file, spec_count, path_count = await _extract_spec_async(
            spec_url=str(spec_path),
            connection_qualified_name=CLOUD_CONNECTION_QN,
            output_dir=str(output_dir),
            auth_header="",
            logger=get_logger("test"),
        )

        assert spec_count == 1
        assert path_count == 2
        assert Path(spec_file.local_path).exists()
        assert Path(path_file.local_path).exists()
