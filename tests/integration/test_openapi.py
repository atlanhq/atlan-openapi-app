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

import orjson
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

# Bundled Petstore v3 spec — avoids depending on petstore3.swagger.io which
# is rate-limited from CI runners. Override OPENAPI_SPEC_URL to test against
# a real external URL instead.
_BUNDLED_SPEC = Path(__file__).parent / "petstore3.json"
_SPEC_URL = os.environ.get("OPENAPI_SPEC_URL") or str(_BUNDLED_SPEC)

CONNECTION_NAME = "test-openapi-integration"
CONNECTION_QN = f"default/api/{CONNECTION_NAME}"

# import_type="URL" fetches the public spec_url directly — no auth, no
# credential resolution (the private-URL Bearer credential is a dropped
# feature).


# Runs entirely in-process (import_type="URL" against a bundled spec; conftest
# mocks all infra), so no emulator is required — just the standard integration
# marker for the tier.
@pytest.mark.integration
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
                    import_type="URL",
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
        """No publish step should occur when load_to_atlan=False."""
        assert extraction_result.publish_completed is False

    async def test_create_usage_is_not_assertion_only(
        self, extraction_result: OpenAPIConnectorOutput
    ) -> None:
        """CONNECT-55: connection_usage=CREATE (the fixture) keeps the normal
        full-diff publish path — assertion_only_enabled must be False."""
        assert extraction_result.assertion_only_enabled is False

    @pytest.fixture(scope="class")
    async def reuse_result(
        self,
        openapi_executor: "AppExecutor",
        tmp_dir_class: Path,
    ) -> OpenAPIConnectorOutput:
        """Execute a REUSE-usage extraction (CONNECT-55).

        Runs the workflow from a class-scoped async fixture — NOT from the test
        body — so it executes on the same session-scoped event loop as the
        ``openapi_worker``/``temporal_client`` fixtures. Driving ``execute_app``
        directly from a (function-scoped) test coroutine submits the workflow on
        a loop the worker never runs, so the worker never polls the task and the
        test hangs until the pytest-timeout guard fires. Mirrors the
        ``extraction_result`` (CREATE) fixture above and the ``*_extraction_result``
        fixtures in the S3/Azure suites, which all run their workflow this way.
        """
        output_dir = tmp_dir_class / "reuse_output"
        output_dir.mkdir(exist_ok=True)

        return cast(
            "OpenAPIConnectorOutput",
            await openapi_executor.execute_app(
                OpenAPIConnector,
                OpenAPIConnectorInput(
                    connection_usage="REUSE",
                    connection_qualified_name=CONNECTION_QN,
                    import_type="URL",
                    spec_url=_SPEC_URL,
                    output_dir=str(output_dir / "run1"),
                    checkpoint_dir="",
                    load_to_atlan=False,
                ),
                execution_id_prefix="test-openapi-reuse",
            ),
        )

    async def test_reuse_usage_enables_assertion_only(
        self,
        reuse_result: OpenAPIConnectorOutput,
        store_root: Path,
    ) -> None:
        """CONNECT-55: connection_usage=REUSE selects an existing connection via
        connection_qualified_name (not the ConnectionCreator) and makes the
        connector emit assertion_only_enabled=True, which the publish node reads
        via `$.extract.outputs.assertion_only_enabled` to run publish-app in
        assertion-only mode (upsert-only, no diff, no deletes). On REUSE the
        connector must NOT emit the Connection entity (the connection already
        exists and must not be re-upserted)."""
        result = reuse_result

        assert result.assertion_only_enabled is True
        assert result.connection_qualified_name == CONNECTION_QN
        # The connector still extracts and transforms normally — assertion-only
        # only changes how publish-app consumes the output, not extraction.
        assert result.api_spec_count >= 1
        assert result.api_path_count >= 1

        # REUSE must NOT emit the Connection entity — only its child assets.
        assert result.output_file is not None
        output_path = store_root / result.output_file.storage_path
        type_names: set[str] = set()
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    record = orjson.loads(line)
                    if "typeName" in record:
                        type_names.add(record["typeName"])
        assert "Connection" not in type_names, (
            "REUSE must not re-emit the Connection entity"
        )
        assert "APISpec" in type_names
        assert "APIPath" in type_names

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
        output_path = store_root / extraction_result.output_file.storage_path
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

    async def test_qualified_names_follow_convention(
        self, extraction_result: OpenAPIConnectorOutput, store_root: Path
    ) -> None:
        """All qualifiedName values should start with the connection prefix."""
        output_path = store_root / extraction_result.output_file.storage_path
        prefix = f"{CONNECTION_QN}/"
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = orjson.loads(line)
                qn = record.get("attributes", {}).get("qualifiedName", "")
                if not qn:
                    qn = record.get("qualifiedName", "")
                if qn and qn != CONNECTION_QN:
                    assert qn.startswith(prefix), (
                        f"qualifiedName '{qn}' does not start with '{prefix}'"
                    )

    async def test_qualified_names_use_api_connector(
        self, extraction_result: OpenAPIConnectorOutput, store_root: Path
    ) -> None:
        """Every emitted qualifiedName — the Connection and all child assets —
        must live under the ``default/api/`` connector prefix, never
        ``default/openapi/`` (the app id). Guards the connector-type segment
        against regression on the CREATE path."""
        # The connector-type segment is the second path component:
        # default/{connectorType}/{epoch}/...
        api_prefix = "default/api/"
        openapi_prefix = "default/openapi/"

        assert extraction_result.connection_qualified_name.startswith(api_prefix), (
            "connection_qualified_name "
            f"'{extraction_result.connection_qualified_name}' must start with "
            f"'{api_prefix}'"
        )

        output_path = store_root / extraction_result.output_file.storage_path
        with output_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = orjson.loads(line)
                qn = record.get("attributes", {}).get("qualifiedName", "")
                if not qn:
                    qn = record.get("qualifiedName", "")
                if not qn:
                    continue
                assert not qn.startswith(openapi_prefix), (
                    f"qualifiedName '{qn}' uses the app id ('{openapi_prefix}') "
                    "instead of the api connector type"
                )
                assert qn.startswith(api_prefix), (
                    f"qualifiedName '{qn}' does not start with '{api_prefix}'"
                )
