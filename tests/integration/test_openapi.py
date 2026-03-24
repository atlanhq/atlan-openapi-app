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

import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
import pytest_asyncio
from temporalio import workflow
from temporalio.client import Client
from temporalio.worker import Worker
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

from application_sdk.contracts.types import ConnectionRef
from application_sdk.execution.sandbox import SandboxConfig
from app.connector import OpenAPIConnector
from app.contracts import (
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
    PublishInput,
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
# Stub publish workflow
# =============================================================================

# Shared list written to by _StubPublishWorkflow (inside the Temporal sandbox)
# and read by TestPublishConnectionEntityCamelCase.  Works because
# "tests.integration.test_openapi" is passed as a sandbox passthrough module —
# the sandbox reuses the already-imported module instance rather than
# re-importing it, so module-level state is shared with the test process.
_captured_publish_inputs: list[dict[str, Any]] = []


@workflow.defn(name="PublishWorkflow")
class _StubPublishWorkflow:
    """Lightweight stand-in for publish-app.

    Records connection_entity from the incoming PublishInput so tests can
    assert on wire-format keys without needing a real publish-app deployment.
    """

    @workflow.run
    async def run(self, input_data: PublishInput) -> dict[str, Any]:
        _captured_publish_inputs.append(input_data.connection_entity)
        return {}


# =============================================================================
# Integration guard: camelCase connection_entity sent to publish-app
# =============================================================================


class TestPublishConnectionEntityCamelCase:
    """Connector must send camelCase connection_entity to publish-app.

    Regression guard for the bug where connection.model_dump() (without
    by_alias=True) serialized ConnectionAttributes fields in snake_case.
    publish-app expects strict camelCase (qualifiedName, connectorName, …).
    """

    @pytest_asyncio.fixture(scope="class")
    async def publish_stub_worker(
        self, temporal_client: Client
    ) -> AsyncGenerator[None, None]:
        """Start a stub 'PublishWorkflow' worker on the publish-app task queue.

        Configures "tests.integration.test_openapi" as a sandbox passthrough so
        the stub workflow can write to _captured_publish_inputs in this module.
        """
        config = SandboxConfig().with_passthrough_modules(
            "tests.integration.test_openapi"
        )
        runner = SandboxedWorkflowRunner(restrictions=config.to_temporal_restrictions())
        w = Worker(
            temporal_client,
            task_queue="atlan-publish-production",
            workflows=[_StubPublishWorkflow],
            workflow_runner=runner,
        )
        async with w:
            yield

    @pytest.fixture(autouse=True)
    def _clear_captures(self) -> Any:
        _captured_publish_inputs.clear()
        yield
        _captured_publish_inputs.clear()

    async def test_connection_entity_keys_are_camel_case(
        self,
        openapi_executor: "AppExecutor",
        publish_stub_worker: None,  # noqa: ARG002 — ensures stub worker is running
        tmp_path: Path,
    ) -> None:
        """Run connector with load_to_atlan=True and verify camelCase keys in connection_entity."""
        output_dir = tmp_path / "camel_case_test"
        output_dir.mkdir()

        await openapi_executor.execute_app(
            OpenAPIConnector,
            OpenAPIConnectorInput(
                connection_usage="CREATE",
                connection=ConnectionRef.model_validate(
                    {
                        "typeName": "Connection",
                        "attributes": {
                            "qualifiedName": "default/api/test-camel-case",
                            "name": "test-camel-case",
                            "connectorName": "api",
                            "category": "API",
                            "adminGroups": ["admins"],
                        },
                    }
                ),
                spec_url=_SPEC_URL,
                output_dir=str(output_dir),
                checkpoint_dir="",
                load_to_atlan=True,
                publish_dry_run=True,
            ),
            execution_id_prefix="test-publish-camel-case",
        )

        assert len(_captured_publish_inputs) == 1, (
            f"Expected exactly one publish-app call, got {len(_captured_publish_inputs)}"
        )
        attrs = _captured_publish_inputs[0].get("attributes", {})
        assert "qualifiedName" in attrs, (
            "publish-app requires camelCase 'qualifiedName' — "
            "connector must call connection.model_dump(by_alias=True)"
        )
        assert "connectorName" in attrs, (
            "publish-app requires camelCase 'connectorName'"
        )
        assert "adminGroups" in attrs, "publish-app requires camelCase 'adminGroups'"
        assert "qualified_name" not in attrs, (
            "snake_case keys must not appear — regression of model_dump() without by_alias=True"
        )
        assert "connector_name" not in attrs
        assert "admin_groups" not in attrs
