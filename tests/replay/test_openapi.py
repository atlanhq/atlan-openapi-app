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

import json
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
                    connection_usage="CREATE",
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
                    load_to_atlan=False,
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

    async def test_no_publish(self, extraction_result: OpenAPIConnectorOutput) -> None:
        """With load_to_atlan=False, publish-app should not be called."""
        assert extraction_result.publish_completed is False

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
