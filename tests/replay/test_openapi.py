"""Replay integration tests for the OpenAPI Connector App.

Runs the full Temporal workflow against deterministic scraped fixtures.
No source credentials or DAPR required — data is frozen from the scrape run.

Requires:
    - Temporal dev server at 127.0.0.1:7233
    - extracts/openapi/replay/ directory from /scrape-source
    - tests/replay/conftest.py

Run with:
    temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true
    uv run pytest tests/replay/test_openapi.py -v
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from pyatlan.models.connection import Connection
from openapi.connector import OpenAPIConnector
from openapi.contracts import (
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
)

if TYPE_CHECKING:
    from app_framework.execution.executor import AppExecutor

# ---------------------------------------------------------------------------
# Load metadata.json — describes exactly what was scraped
# ---------------------------------------------------------------------------
_METADATA_PATH = (
    Path(__file__).parent.parent.parent / "extracts" / "openapi" / "metadata.json"
)
_METADATA = json.loads(_METADATA_PATH.read_text())

# The connector's total_scanned = api_spec_count + api_path_count (Connection is emitted
# unconditionally and is not subject to change detection, so it is excluded).
# metadata.json entity_types records the spec-level records (APISpec + APIPath = 14 for Petstore).
_EXPECTED_SPEC_RECORDS = sum(
    v["records"] for v in _METADATA["entity_types"].values()
)  # 14
_EXPECTED_TOTAL = _EXPECTED_SPEC_RECORDS  # 14

# No pytestmark skip — replay tests never require source credentials.
# Public Petstore spec URL is read from metadata.json.
_SPEC_URL = _METADATA.get(
    "base_url", "https://petstore3.swagger.io/api/v3/openapi.json"
)

CONNECTION_NAME = "test-openapi-replay"
CONNECTION_QN = f"default/api/{CONNECTION_NAME}"


class TestReplayExtraction:
    """Full extraction against frozen replay fixtures.

    Uses a class-scoped fixture to run the workflow once and share the
    result across all test methods (avoids re-running the expensive workflow).
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("openapi_replay_extraction")

    @pytest.fixture(scope="class")
    async def extraction_result(
        self,
        replay_executor: "AppExecutor",
        mock_openapi_spec,  # noqa: ARG002 — ensures mock is active before workflow runs
        tmp_dir_class: Path,
    ) -> OpenAPIConnectorOutput:
        """Execute a full extraction against scraped replay fixtures."""
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        result = cast(
            "OpenAPIConnectorOutput",
            await replay_executor.execute_app(
                OpenAPIConnector,
                OpenAPIConnectorInput(
                    connection=Connection(
                        qualified_name=CONNECTION_QN,
                        name=CONNECTION_NAME,
                        category="API",
                        admin_groups=["admins"],
                    ),
                    spec_url=_SPEC_URL,
                    openapi_credential=None,
                    output_dir=str(output_dir / "run1"),
                    checkpoint_dir="",
                    load_to_atlan=True,
                    loader_dry_run=True,
                    atlan_credential=None,
                ),
                execution_id_prefix="test-openapi-replay-extraction",
            ),
        )
        return result

    async def test_workflow_completes(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Workflow should complete without error."""
        assert extraction_result is not None

    async def test_exact_record_count(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Should extract exactly as many records as were scraped (frozen data)."""
        assert extraction_result.total_scanned == _EXPECTED_TOTAL, (
            f"Expected {_EXPECTED_TOTAL} records (from metadata.json), "
            f"got {extraction_result.total_scanned}"
        )

    async def test_all_new_without_checkpoint(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Without checkpoint, all records should be NEW."""
        assert extraction_result.new_count == extraction_result.total_scanned
        assert extraction_result.unchanged_count == 0
        assert extraction_result.changed_count == 0
        assert extraction_result.deleted_count == 0

    async def test_dry_run_validation_passed(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Atlan-loader should validate all extracted assets in dry-run mode."""
        assert extraction_result.atlan_validated_count > 0
        assert extraction_result.atlan_error_count == 0

    async def test_no_actual_loading(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """Dry-run should not load any assets to Atlan."""
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
        import json as _json

        output_path = Path(extraction_result.output_file.local_path)
        type_names: set[str] = set()
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    record = _json.loads(line)
                    if "typeName" in record:
                        type_names.add(record["typeName"])

        assert "Connection" in type_names
        assert "APISpec" in type_names
        assert "APIPath" in type_names

    async def test_per_type_counts(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """APISpec count should be 1; APIPath count should be the remainder minus Connection."""
        assert extraction_result.api_spec_count == 1
        # total = 1 APISpec + N APIPath (Connection excluded), so api_path_count = total - 1
        assert extraction_result.api_path_count == _EXPECTED_TOTAL - 1

    async def test_qualified_names_follow_convention(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """All qualifiedName values should start with the connection QN prefix."""
        import json as _json

        output_path = Path(extraction_result.output_file.local_path)
        prefix = f"{CONNECTION_QN}/"
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = _json.loads(line)
                qn = record.get("attributes", {}).get("qualifiedName", "")
                if not qn:
                    qn = record.get("qualifiedName", "")
                if qn and qn != CONNECTION_QN:
                    assert qn.startswith(prefix), (
                        f"qualifiedName '{qn}' does not start with '{prefix}'"
                    )


class TestReplayCheckpoint:
    """Incremental extraction checkpoint test using frozen replay data.

    First run populates checkpoint (all NEW), second run verifies all UNCHANGED.
    Data is identical on both runs because fixtures are deterministic.
    """

    @pytest.fixture(scope="class")
    def tmp_dir_class(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        return tmp_path_factory.mktemp("openapi_replay_checkpoint")

    @pytest.fixture(scope="class")
    async def first_run_result(
        self,
        replay_executor: "AppExecutor",
        mock_openapi_spec,  # noqa: ARG002 — ensures mock is active
        tmp_dir_class: Path,
    ) -> OpenAPIConnectorOutput:
        """Execute first run to populate checkpoint."""
        checkpoint_dir = tmp_dir_class / "checkpoint"
        checkpoint_dir.mkdir()
        output_dir = tmp_dir_class / "output"
        output_dir.mkdir()

        result = cast(
            "OpenAPIConnectorOutput",
            await replay_executor.execute_app(
                OpenAPIConnector,
                OpenAPIConnectorInput(
                    connection=Connection(
                        qualified_name=CONNECTION_QN,
                        name=CONNECTION_NAME,
                        category="API",
                        admin_groups=["admins"],
                    ),
                    spec_url=_SPEC_URL,
                    openapi_credential=None,
                    output_dir=str(output_dir / "run1"),
                    checkpoint_dir=str(checkpoint_dir),
                    load_to_atlan=True,
                    loader_dry_run=True,
                    atlan_credential=None,
                ),
                execution_id_prefix="test-openapi-replay-checkpoint-run1",
            ),
        )
        return result

    async def test_first_run_all_new(
        self, first_run_result: OpenAPIConnectorOutput
    ) -> None:
        """First run with checkpoint should mark all records as NEW."""
        # Connection is excluded from total_scanned, so new_count == total_scanned.
        assert first_run_result.new_count == first_run_result.total_scanned
        assert first_run_result.unchanged_count == 0
        assert first_run_result.total_scanned == _EXPECTED_TOTAL
        assert first_run_result.atlan_validated_count > 0

    @pytest.fixture(scope="class")
    async def second_run_result(
        self,
        replay_executor: "AppExecutor",
        mock_openapi_spec,  # noqa: ARG002 — ensures mock is active
        first_run_result: OpenAPIConnectorOutput,  # noqa: ARG002 — ensures run1 completes first
        tmp_dir_class: Path,
    ) -> OpenAPIConnectorOutput:
        """Execute second run (same frozen data) to verify all records are UNCHANGED."""
        # Small delay for epoch-based checkpoint tracking — must be non-blocking
        await asyncio.sleep(1.5)

        checkpoint_dir = tmp_dir_class / "checkpoint"
        output_dir = tmp_dir_class / "output"

        return cast(
            "OpenAPIConnectorOutput",
            await replay_executor.execute_app(
                OpenAPIConnector,
                OpenAPIConnectorInput(
                    connection=Connection(
                        qualified_name=CONNECTION_QN,
                        name=CONNECTION_NAME,
                        category="API",
                        admin_groups=["admins"],
                    ),
                    spec_url=_SPEC_URL,
                    openapi_credential=None,
                    output_dir=str(output_dir / "run2"),
                    checkpoint_dir=str(checkpoint_dir),
                    load_to_atlan=True,
                    loader_dry_run=True,
                    atlan_credential=None,
                ),
                execution_id_prefix="test-openapi-replay-checkpoint-run2",
            ),
        )

    async def test_second_run_all_unchanged(
        self,
        first_run_result: OpenAPIConnectorOutput,
        second_run_result: OpenAPIConnectorOutput,
    ) -> None:
        """Second run with identical frozen data should mark all records as UNCHANGED."""
        # unchanged_count = new_count from run1 (diff tracks APISpec+APIPath only, not Connection)
        assert second_run_result.unchanged_count == first_run_result.new_count
        assert second_run_result.new_count == 0
        assert second_run_result.changed_count == 0
        assert second_run_result.deleted_count == 0
        assert (
            second_run_result.output_file is None
        )  # No changes to transform — loader has nothing to validate
