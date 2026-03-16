"""Integration tests for the OpenAPI Connector App.

Tests the full extraction workflow through Temporal.
Validates extraction, change detection, transform, and output file content.

The OpenAPI connector works with public spec URLs — no API credentials needed
for public specs (e.g. the Swagger Petstore).

Requires:
    - Temporal cluster at 127.0.0.1:7233
    - temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true
    - dapr CLI installed

Run with:
    make test-integration   # ALWAYS use make — sets OTEL_EXPORTER_OTLP_ENDPOINT
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from pyatlan_v9.model.assets import Connection
from openapi.connector import OpenAPIConnector
from openapi.contracts import (
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
)

if TYPE_CHECKING:
    from app_framework.execution.executor import AppExecutor

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
                    connection=Connection(
                        qualified_name=CONNECTION_QN,
                        name=CONNECTION_NAME,
                        category="API",
                        admin_groups=["admins"],
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

    async def test_all_new_without_checkpoint(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Without checkpoint, new_count equals total_scanned."""
        assert extraction_result.new_count == extraction_result.total_scanned
        assert extraction_result.unchanged_count == 0
        assert extraction_result.changed_count == 0
        assert extraction_result.deleted_count == 0

    async def test_no_atlan_loading(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """No Atlan loading should occur when load_to_atlan=False."""
        assert extraction_result.atlan_loaded_count == 0

    async def test_output_file_exists(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Output JSONL file should exist and be non-empty."""
        assert extraction_result.output_file is not None
        output_path = Path(extraction_result.output_file.local_path)
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    async def test_output_contains_expected_types(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Output JSONL should contain Connection, APISpec, and APIPath."""
        import json

        output_path = Path(extraction_result.output_file.local_path)
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
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """All qualifiedName values should start with the connection prefix."""
        import json

        output_path = Path(extraction_result.output_file.local_path)
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


class TestOpenAPIConnectorCheckpoint:
    """Incremental extraction with checkpoint.

    First run populates checkpoint (all NEW), second run verifies all UNCHANGED.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("openapi_checkpoint")

    @pytest.fixture(scope="class")
    async def first_run_result(
        self,
        openapi_executor: "AppExecutor",
        tmp_dir_class: Path,
    ) -> OpenAPIConnectorOutput:
        """Execute first run to populate checkpoint."""
        checkpoint_dir = tmp_dir_class / "checkpoint"
        checkpoint_dir.mkdir()
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        result = cast(
            "OpenAPIConnectorOutput",
            await openapi_executor.execute_app(
                OpenAPIConnector,
                OpenAPIConnectorInput(
                    connection=Connection(
                        qualified_name=CONNECTION_QN,
                        name=CONNECTION_NAME,
                        category="API",
                        admin_groups=["admins"],
                    ),
                    spec_url=_SPEC_URL,
                    output_dir=str(output_dir / "run1"),
                    checkpoint_dir=str(checkpoint_dir),
                    load_to_atlan=False,
                ),
                execution_id_prefix="test-openapi-checkpoint-run1",
            ),
        )
        return result

    async def test_first_run_all_new(
        self, first_run_result: OpenAPIConnectorOutput
    ) -> None:
        """First run with checkpoint should mark all records as NEW."""
        assert first_run_result.new_count == first_run_result.total_scanned
        assert first_run_result.unchanged_count == 0
        assert first_run_result.changed_count == 0
        assert first_run_result.deleted_count == 0
        assert first_run_result.total_scanned >= 2

    async def test_second_run_all_unchanged(
        self,
        openapi_executor: "AppExecutor",
        first_run_result: OpenAPIConnectorOutput,
        tmp_dir_class: Path,
    ) -> None:
        """Second run with same spec should mark all records as UNCHANGED."""
        import time

        time.sleep(1.5)

        checkpoint_dir = tmp_dir_class / "checkpoint"
        output_dir = tmp_dir_class / "output"

        result = cast(
            "OpenAPIConnectorOutput",
            await openapi_executor.execute_app(
                OpenAPIConnector,
                OpenAPIConnectorInput(
                    connection=Connection(
                        qualified_name=CONNECTION_QN,
                        name=CONNECTION_NAME,
                        category="API",
                        admin_groups=["admins"],
                    ),
                    spec_url=_SPEC_URL,
                    output_dir=str(output_dir / "run2"),
                    checkpoint_dir=str(checkpoint_dir),
                    load_to_atlan=False,
                ),
                execution_id_prefix="test-openapi-checkpoint-run2",
            ),
        )

        assert result.unchanged_count == first_run_result.total_scanned
        assert result.new_count == 0
        assert result.changed_count == 0
        assert result.deleted_count == 0
        assert result.output_file is None  # No changes to transform
