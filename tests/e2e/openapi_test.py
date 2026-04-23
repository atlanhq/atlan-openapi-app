"""Kubernetes-based end-to-end test for the OpenAPI connector to Atlan loader flow.

Usage:
    python -m tests.e2e.openapi_test
    python -m tests.e2e.openapi_test --skip-deploy
    python -m tests.e2e.openapi_test --skip-cleanup --skip-undeploy

Prerequisites:
    - Kubernetes cluster accessible via kubectl
    - Temporal deployed in the cluster (namespace: temporal)
    - Environment variables: GH_USERNAME, APP_PKG_GH_PAT, ATLAN_BASE_URL, ATLAN_API_KEY
    - OPENAPI_SPEC_URL (optional — defaults to public Petstore spec)
"""

from __future__ import annotations

import asyncio
import orjson
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from application_sdk.contracts.types import ConnectionRef

from app.contracts import OpenAPIConnectorInput
from tests.e2e.infra import (
    AppConfig,
    AssetTypeSpec,
    LogCollector,
    create_base_argument_parser,
    deploy_app,
    format_duration,
    get_timing,
    output_result,
    run_workflow,
    undeploy_app,
    validate_env_vars,
    verify_atlan_assets,
)

# =============================================================================
# OpenAPI-specific configuration
# =============================================================================

OPENAPI_APP = AppConfig(
    name="openapi",
    module="app.connector:OpenAPIConnector",
    namespace="app-openapi",
    task_queue="openapi-queue",
    values_file=Path(__file__).parent.parent.parent / "helm" / "values.yaml",
)

# Asset types that must exist in Atlan after a successful run
OPENAPI_EXPECTED_TYPES = [
    AssetTypeSpec("APISpec", min_count=1),
    AssetTypeSpec("APIPath", min_count=1),
]

# Default to the public Swagger Petstore — no credentials required.
_DEFAULT_SPEC_URL = "https://petstore3.swagger.io/api/v3/openapi.json"


# =============================================================================
# Test result
# =============================================================================


@dataclass
class OpenAPITestResult:
    """Results from the OpenAPI E2E test."""

    success: bool = False
    started_at: str = ""
    completed_at: str = ""

    # Connector results
    workflow_id: str = ""
    correlation_id: str = ""
    workflow_duration_ms: float = 0.0
    task_timings: list[dict[str, Any]] = field(default_factory=list)

    # Asset counts
    api_spec_count: int = 0
    api_path_count: int = 0
    total_scanned: int = 0
    publish_completed: bool = False

    # Validation results
    expected_asset_count: int = 0
    actual_asset_count: int = 0
    creation_success_rate: float = 0.0
    validation_passed: bool = False

    # Atlan direct verification
    atlan_verification_passed: bool = False
    atlan_verification_type_counts: dict[str, int] = field(default_factory=dict)
    atlan_verification_unexpected_asset_count: int = 0
    atlan_verification_failures: list[str] = field(default_factory=list)
    atlan_verification_warnings: list[str] = field(default_factory=list)

    errors: list[str] = field(default_factory=list)


def format_result(result: OpenAPITestResult, connection_name: str) -> str:
    """Format the test result for display."""
    lines = [
        "",
        "=" * 70,
        "OPENAPI CONNECTOR E2E TEST RESULTS",
        "=" * 70,
        f"  Status:       {'SUCCESS' if result.success else 'FAILED'}",
        f"  Connection:   {connection_name}",
        f"  Workflow ID:  {result.workflow_id}",
        f"  Correlation:  {result.correlation_id}",
        "",
        "--- Timing ---",
        f"  Total duration:  {format_duration(result.workflow_duration_ms)}",
    ]

    if result.task_timings:
        for t in result.task_timings:
            name = t.get("name", "?")
            dur = t.get("duration_ms", 0)
            skipped = t.get("skipped", False)
            if skipped:
                lines.append(f"    {name:.<30} skipped")
            else:
                lines.append(f"    {name:.<30} {format_duration(dur)}")

    lines.extend(
        [
            "",
            "--- Assets ---",
            f"  APISpec count: {result.api_spec_count}",
            f"  APIPath count: {result.api_path_count}",
            f"  Total scanned: {result.total_scanned}",
            "",
            "--- Publishing ---",
            f"  publish_completed: {result.publish_completed}",
            "",
            "--- Validation ---",
            f"  Expected assets:    {result.expected_asset_count}",
            f"  Actual created:     {result.actual_asset_count}",
            f"  Success rate:       {result.creation_success_rate:.1f}%",
            f"  Validation passed:  {result.validation_passed}",
            "",
            "--- Atlan Asset Verification ---",
        ]
    )
    for type_name, count in result.atlan_verification_type_counts.items():
        lines.append(f"  {type_name + ':':<40} {count}")
    lines.extend(
        [
            f"  Unexpected 'Asset' type:             {result.atlan_verification_unexpected_asset_count}",
            f"  Passed:                              {result.atlan_verification_passed}",
        ]
    )
    for warn in result.atlan_verification_warnings:
        lines.append(f"  [WARNING] {warn}")
    for fail in result.atlan_verification_failures:
        lines.append(f"  [FAILED]  {fail}")

    if result.errors:
        lines.append("")
        lines.append("--- Errors ---")
        for error in result.errors:
            lines.append(f"  - {error}")

    lines.append("=" * 70)
    return "\n".join(lines)


# =============================================================================
# Test runner
# =============================================================================


class OpenAPITestRunner:
    """Runs the OpenAPI connector E2E test and collects results."""

    async def run_test(
        self,
        connection_name: str,
        spec_url: str,
        log_collector: LogCollector | None = None,
    ) -> OpenAPITestResult:
        """Run the full OpenAPI connector E2E test."""
        result = OpenAPITestResult(started_at=datetime.now(UTC).isoformat())

        print("\n" + "=" * 70)
        print("OpenAPI Connector E2E Test")
        print("=" * 70)
        print(f"  Spec URL: {spec_url}")

        try:
            # ============================================================
            # Step 1: Run connector with Atlan loading (via publish-app)
            # ============================================================
            print("\n[Step 1] Running OpenAPI connector (with Atlan loading)...")
            connector_input = OpenAPIConnectorInput(
                connection_usage="CREATE",
                connection=ConnectionRef.model_validate(
                    {
                        "typeName": "Connection",
                        "attributes": {
                            "qualifiedName": f"default/api/{connection_name}",
                            "name": connection_name,
                            "connectorName": "api",
                            "category": "API",
                            "adminGroups": ["admins"],
                        },
                    }
                ),
                spec_url=spec_url,
                load_to_atlan=True,
                checkpoint_dir=f"/tmp/openapi/checkpoint/{connection_name}",
            )

            connector_result, workflow_id, correlation_id = await run_workflow(
                OPENAPI_APP, connector_input
            )
            result.workflow_id = workflow_id
            result.correlation_id = correlation_id
            if log_collector:
                log_collector.record(OPENAPI_APP, correlation_id, "pass1-connector")

            result.api_spec_count = connector_result.get("api_spec_count", 0)
            result.api_path_count = connector_result.get("api_path_count", 0)
            result.total_scanned = connector_result.get("total_scanned", 0)
            result.publish_completed = connector_result.get("publish_completed", False)
            print(f"  APISpec count:      {result.api_spec_count}")
            print(f"  APIPath count:      {result.api_path_count}")
            print(f"  Total scanned:      {result.total_scanned}")
            print(f"  Publish completed:  {result.publish_completed}")

            # ============================================================
            # Step 2: Run second pass (incremental diff)
            # ============================================================
            print("\n[Step 2] Running second pass (incremental diff)...")
            pass2_result, _, pass2_corr_id = await run_workflow(
                OPENAPI_APP, connector_input
            )
            if log_collector:
                log_collector.record(OPENAPI_APP, pass2_corr_id, "pass2-incremental")
            pass2_unchanged_count = pass2_result.get("unchanged_count", 0)
            print(f"  Unchanged: {pass2_unchanged_count}")

            # ============================================================
            # Step 3: Validate publish completed
            # ============================================================
            print("\n[Step 3] Validating publish-app was called...")
            result.validation_passed = result.publish_completed
            if not result.validation_passed:
                result.errors.append(
                    "publish-app was not called (publish_completed=False)"
                )

            # ============================================================
            # Step 4: Verify assets in Atlan
            # ============================================================
            print("\n[Step 4] Verifying assets in Atlan...")
            connection_qn = f"default/api/{connection_name}"
            verification = await verify_atlan_assets(
                connection_qn, OPENAPI_EXPECTED_TYPES
            )
            result.atlan_verification_passed = verification.passed
            result.atlan_verification_type_counts = verification.type_counts
            result.atlan_verification_unexpected_asset_count = (
                verification.unexpected_asset_count
            )
            result.atlan_verification_failures = verification.failures
            result.atlan_verification_warnings = verification.warnings
            if not verification.passed:
                result.errors.extend(
                    [f"Atlan verification: {f}" for f in verification.failures]
                )

            # ============================================================
            # Step 5: Fetch timing breakdown
            # ============================================================
            print("\n[Step 5] Fetching timing breakdown...")
            duration_ms, task_timings = await get_timing(OPENAPI_APP, workflow_id)
            result.workflow_duration_ms = duration_ms
            result.task_timings = task_timings
            print(f"  Total duration: {format_duration(duration_ms)}")

            result.success = (
                result.publish_completed
                and result.validation_passed
                and result.atlan_verification_passed
            )

        except Exception as e:
            result.errors.append(f"Test failed: {e}")
            import traceback

            traceback.print_exc()

        result.completed_at = datetime.now(UTC).isoformat()
        return result


# =============================================================================
# Main
# =============================================================================


async def main() -> int:
    """Main entry point."""
    sys.stdout.reconfigure(line_buffering=True)

    parser = create_base_argument_parser("Run OpenAPI connector E2E test on Kubernetes")
    parser.add_argument(
        "--connection-name",
        type=str,
        default=None,
        help="Atlan connection name (default: epoch timestamp)",
    )
    parser.add_argument(
        "--spec-url",
        type=str,
        default=None,
        help=f"OpenAPI spec URL (default: {_DEFAULT_SPEC_URL})",
    )
    args = parser.parse_args()

    if args.connection_name is None:
        args.connection_name = str(int(time.time()))

    spec_url = args.spec_url or os.environ.get("OPENAPI_SPEC_URL", _DEFAULT_SPEC_URL)

    missing = validate_env_vars([])
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        return 1

    if not os.environ.get("ATLAN_BASE_URL") or not os.environ.get("ATLAN_API_KEY"):
        print("ERROR: ATLAN_BASE_URL and ATLAN_API_KEY are required")
        return 1

    log_collector = LogCollector(
        test_name="openapi",
        apps=[OPENAPI_APP],
        log_dir=Path(args.log_dir) if args.log_dir else None,
    )

    try:
        if not args.skip_deploy:
            print("\n--- Deploying app ---")
            atlan_api_key = os.environ["ATLAN_API_KEY"]
            await deploy_app(
                OPENAPI_APP,
                image_tag=args.image_tag,
                extra_helm_sets=[
                    f"connectorCredentials.atlan.data.token={atlan_api_key}",
                ],
            )

        runner = OpenAPITestRunner()
        result = await runner.run_test(
            connection_name=args.connection_name,
            spec_url=spec_url,
            log_collector=log_collector,
        )

        if args.format == "json":
            output_data = {
                "success": result.success,
                "started_at": result.started_at,
                "completed_at": result.completed_at,
                "workflow_id": result.workflow_id,
                "correlation_id": result.correlation_id,
                "timing": {
                    "total_duration_ms": result.workflow_duration_ms,
                    "tasks": result.task_timings,
                },
                "assets": {
                    "api_spec_count": result.api_spec_count,
                    "api_path_count": result.api_path_count,
                    "total_scanned": result.total_scanned,
                },
                "publishing": {
                    "publish_completed": result.publish_completed,
                },
                "validation": {
                    "expected_asset_count": result.expected_asset_count,
                    "actual_asset_count": result.actual_asset_count,
                    "creation_success_rate": result.creation_success_rate,
                    "passed": result.validation_passed,
                },
                "atlan_verification": {
                    "passed": result.atlan_verification_passed,
                    "type_counts": result.atlan_verification_type_counts,
                    "unexpected_asset_count": result.atlan_verification_unexpected_asset_count,
                    "failures": result.atlan_verification_failures,
                    "warnings": result.atlan_verification_warnings,
                },
                "errors": result.errors,
            }
            output_text = orjson.dumps(output_data, option=orjson.OPT_INDENT_2).decode()
        else:
            output_text = format_result(result, args.connection_name)

        output_result(output_text, args.output)
        return 0 if result.success else 1

    finally:
        if not args.skip_deploy:
            await log_collector.collect_all()
        if not args.skip_undeploy and not args.skip_deploy:
            print("\n--- Undeploying app ---")
            await undeploy_app(OPENAPI_APP)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
