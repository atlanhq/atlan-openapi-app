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
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.e2e.infra import (
    DELETE_APP,
    LOADER_APP,
    AppConfig,
    AssetTypeSpec,
    LogCollector,
    MultiAppDeployer,
    atlan_credential_dict,
    create_base_argument_parser,
    delete_helm_creds,
    format_duration,
    get_timing,
    loader_helm_creds,
    output_result,
    run_delete_workflow,
    run_workflow,
    validate_asset_creation,
    validate_deletion,
    validate_env_vars,
    verify_atlan_assets,
)

# =============================================================================
# OpenAPI-specific configuration
# =============================================================================

OPENAPI_APP = AppConfig(
    name="openapi",
    module="openapi.connector:OpenAPIConnector",
    namespace="app-openapi",
    task_queue="openapi-queue",
    values_file=Path(__file__).parent.parent.parent / "values.yaml",
)

CUSTOM_TYPEDEFS_APP = AppConfig(
    name="custom-typedefs",
    module="custom_typedefs.custom_typedefs:CustomTypedefs",
    namespace="app-custom-typedefs",
    task_queue="custom-typedefs-queue",
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
    atlan_loaded_count: int = 0
    atlan_created_count: int = 0
    atlan_updated_count: int = 0

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

    # Diff validation (second pass)
    pass2_unchanged_count: int = 0
    pass2_new_count: int = 0
    pass2_changed_count: int = 0
    diff_validation_passed: bool = False

    # Deletion results
    deletion_ran: bool = False
    total_assets_deleted: int = 0
    connection_deleted: bool = False

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
            "--- Atlan Loading ---",
            f"  Loaded:   {result.atlan_loaded_count}",
            f"  Created:  {result.atlan_created_count}",
            f"  Updated:  {result.atlan_updated_count}",
            "",
            "--- Diff Validation (Second Pass) ---",
            f"  Unchanged: {result.pass2_unchanged_count}",
            f"  New:       {result.pass2_new_count}",
            f"  Changed:   {result.pass2_changed_count}",
            f"  Passed:    {result.diff_validation_passed}",
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
    lines.extend(
        [
            "",
            "--- Cleanup ---",
            f"  Deletion ran:         {result.deletion_ran}",
            f"  Assets deleted:       {result.total_assets_deleted}",
            f"  Connection deleted:   {result.connection_deleted}",
        ]
    )

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
        skip_cleanup: bool = False,
        batch_size: int | None = None,
        save_timeout: float | None = None,
        log_collector: LogCollector | None = None,
    ) -> OpenAPITestResult:
        """Run the full OpenAPI connector E2E test."""
        result = OpenAPITestResult(started_at=datetime.now(UTC).isoformat())

        atlan_credential = atlan_credential_dict()

        print("\n" + "=" * 70)
        print("OpenAPI Connector E2E Test")
        print("=" * 70)
        print(f"  Spec URL: {spec_url}")

        try:
            # ============================================================
            # Step 1: Run connector with Atlan loading
            # ============================================================
            print("\n[Step 1] Running OpenAPI connector (with Atlan loading)...")
            connector_input: dict[str, Any] = {
                "connection": {
                    "typeName": "Connection",
                    "qualifiedName": f"default/api/{connection_name}",
                    "name": connection_name,
                },
                "spec_url": spec_url,
                "load_to_atlan": True,
                "atlan_credential": atlan_credential,
                "checkpoint_dir": f"/tmp/openapi/checkpoint/{connection_name}",
            }
            if batch_size is not None:
                connector_input["loader_batch_size"] = batch_size
            if save_timeout is not None:
                connector_input["loader_save_timeout"] = save_timeout

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
            result.atlan_loaded_count = connector_result.get("atlan_loaded_count", 0)
            result.atlan_created_count = connector_result.get("atlan_created_count", 0)
            result.atlan_updated_count = connector_result.get("atlan_updated_count", 0)
            print(f"  APISpec count:   {result.api_spec_count}")
            print(f"  APIPath count:   {result.api_path_count}")
            print(f"  Total scanned:   {result.total_scanned}")
            print(f"  Loaded to Atlan: {result.atlan_loaded_count}")

            # ============================================================
            # Step 2: Run second pass (incremental diff)
            # ============================================================
            print("\n[Step 2] Running second pass (incremental diff)...")
            pass2_result, _, pass2_corr_id = await run_workflow(
                OPENAPI_APP, connector_input
            )
            if log_collector:
                log_collector.record(OPENAPI_APP, pass2_corr_id, "pass2-incremental")
            result.pass2_unchanged_count = pass2_result.get("unchanged_count", 0)
            result.pass2_new_count = pass2_result.get("new_count", 0)
            result.pass2_changed_count = pass2_result.get("changed_count", 0)
            result.diff_validation_passed = result.pass2_unchanged_count > 0
            print(f"  Unchanged: {result.pass2_unchanged_count}")
            if not result.diff_validation_passed:
                result.errors.append("Second pass should detect unchanged assets")

            # ============================================================
            # Step 3: Validate asset creation
            # ============================================================
            print("\n[Step 3] Validating asset creation...")
            result.expected_asset_count = result.total_scanned
            result.validation_passed, result.creation_success_rate = (
                validate_asset_creation(
                    result.expected_asset_count,
                    result.atlan_created_count,
                    result.atlan_updated_count,
                )
            )
            result.actual_asset_count = (
                result.atlan_created_count + result.atlan_updated_count
            )
            print(f"  Success rate: {result.creation_success_rate:.1f}%")
            if not result.validation_passed:
                result.errors.append(
                    f"Asset creation validation FAILED: {result.creation_success_rate:.1f}% "
                    f"({result.actual_asset_count}/{result.expected_asset_count}). Min: 90.0%"
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

            # ============================================================
            # Step 6: Clean up
            # ============================================================
            if not skip_cleanup:
                print(f"\n[Step 6] Deleting connection '{connection_name}'...")
                try:
                    checkpoint_ref = connector_result.get("checkpoint_ref")
                    refs = [checkpoint_ref] if checkpoint_ref else []
                    delete_result, _, delete_corr_id = await run_delete_workflow(
                        DELETE_APP,
                        connection_name=connection_name,
                        connector_type="api",
                        credential=atlan_credential,
                        workflow_id_prefix="openapi-test-cleanup",
                        checkpoint_refs=refs,
                    )
                    if log_collector:
                        log_collector.record(
                            DELETE_APP, delete_corr_id, "delete-cleanup"
                        )
                    result.deletion_ran = True
                    result.total_assets_deleted = delete_result.get(
                        "total_assets_deleted", 0
                    )
                    result.connection_deleted = delete_result.get(
                        "connection_deleted", False
                    )
                    warn = validate_deletion(
                        result.total_assets_deleted, result.actual_asset_count
                    )
                    if warn:
                        result.errors.append(f"Warning: {warn}")
                except Exception as e:
                    result.errors.append(f"Cleanup failed: {e}")
            else:
                print("\n[Step 6] Skipping cleanup (--skip-cleanup)")

            result.success = (
                result.atlan_loaded_count > 0
                and result.validation_passed
                and result.diff_validation_passed
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

    missing = validate_env_vars([], skip_deploy=args.skip_deploy)
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        return 1

    if not os.environ.get("ATLAN_BASE_URL") or not os.environ.get("ATLAN_API_KEY"):
        print("ERROR: ATLAN_BASE_URL and ATLAN_API_KEY are required")
        return 1

    all_apps = [OPENAPI_APP, CUSTOM_TYPEDEFS_APP, LOADER_APP, DELETE_APP]
    deployer = MultiAppDeployer(apps=all_apps, image_tag=args.image_tag)

    log_collector = LogCollector(
        test_name="openapi",
        apps=all_apps,
        log_dir=Path(args.log_dir) if args.log_dir else None,
    )

    try:
        if not args.skip_deploy:
            print("\n--- Deploying apps ---")

            atlan_api_key = os.environ["ATLAN_API_KEY"]

            openapi_creds = [
                f"connectorCredentials.atlan.data.token={atlan_api_key}",
            ]

            await deployer.deploy_all(
                credentials={
                    OPENAPI_APP.name: openapi_creds,
                    LOADER_APP.name: loader_helm_creds(),
                    DELETE_APP.name: delete_helm_creds(),
                }
            )

        runner = OpenAPITestRunner()
        result = await runner.run_test(
            connection_name=args.connection_name,
            spec_url=spec_url,
            skip_cleanup=args.skip_cleanup,
            batch_size=args.batch_size,
            save_timeout=args.save_timeout,
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
                "loading": {
                    "loaded": result.atlan_loaded_count,
                    "created": result.atlan_created_count,
                    "updated": result.atlan_updated_count,
                },
                "diff_validation": {
                    "pass2_unchanged_count": result.pass2_unchanged_count,
                    "passed": result.diff_validation_passed,
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
                "cleanup": {
                    "ran": result.deletion_ran,
                    "assets_deleted": result.total_assets_deleted,
                    "connection_deleted": result.connection_deleted,
                },
                "errors": result.errors,
            }
            output_text = json.dumps(output_data, indent=2)
        else:
            output_text = format_result(result, args.connection_name)

        output_result(output_text, args.output)
        return 0 if result.success else 1

    finally:
        if not args.skip_deploy:
            await log_collector.collect_all()
        if not args.skip_undeploy and not args.skip_deploy:
            print("\n--- Undeploying apps ---")
            await deployer.undeploy_all()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
