"""Local dry-run regression tests for the OpenAPI connector.

Runs the connector in dry-run mode against a local Temporal server with
DAPR secret stores. No Kubernetes or Atlan credentials required for dry-run.

Prerequisites:
    - temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true
    - dapr CLI installed (dapr init --slim)
    - ATLAN_API_KEY (for loader dry-run validation)
    - OPENAPI_SPEC_URL (optional — defaults to public Petstore spec)

Usage:
    python -m tests.e2e.local_regression_test
    python -m tests.e2e.local_regression_test --tests openapi
    python -m tests.e2e.local_regression_test --format json
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from tests.e2e.local_infra import (
    LOADER_LOCAL,
    OPENAPI_LOCAL,
    LocalProcessManager,
    create_local_argument_parser,
    prepare_dapr_components,
)
from tests.e2e.infra import run_workflow_local


# =============================================================================
# Local test result
# =============================================================================


@dataclass
class LocalTestResult:
    """Result of a single local regression test."""

    name: str
    status: str = "PENDING"  # PASS | FAIL | SKIPPED
    duration_s: float = 0.0
    assets_extracted: int = 0
    dry_run_validated: int = 0
    dry_run_error_count: int = 0
    dry_run_errors: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# =============================================================================
# DAPR secrets builder
# =============================================================================


def _build_dapr_secrets() -> dict[str, str]:
    """Build DAPR secrets from environment variables."""
    secrets: dict[str, str] = {}

    # Atlan API token (for dry-run validation)
    atlan_key = os.environ.get("ATLAN_API_KEY", "")
    if atlan_key:
        secrets["atlan"] = json.dumps(
            {
                "type": "atlan_api_token",
                "token": atlan_key,
                "base_url": os.environ.get("ATLAN_BASE_URL", ""),
            }
        )

    # OpenAPI credential (optional — only needed for private spec endpoints)
    openapi_auth_header = os.environ.get("OPENAPI_AUTH_HEADER", "")
    if openapi_auth_header:
        secrets["openapi"] = json.dumps(
            {
                "type": "openapi",
                "auth_header": openapi_auth_header,
            }
        )

    return secrets


# =============================================================================
# OpenAPI local regression test
# =============================================================================

_DEFAULT_SPEC_URL = "https://petstore3.swagger.io/api/v3/openapi.json"


async def run_openapi_test(mgr: LocalProcessManager) -> LocalTestResult:
    """Run the OpenAPI connector in dry-run mode."""
    result = LocalTestResult(name="openapi")

    spec_url = os.environ.get("OPENAPI_SPEC_URL", _DEFAULT_SPEC_URL)

    t0 = time.monotonic()
    try:
        print("    Starting OpenAPI connector processes...")
        mgr.start_app(OPENAPI_LOCAL)

        _conn_name = f"local-openapi-{int(time.time())}"
        connector_input: dict[str, Any] = {
            "connection": {
                "typeName": "Connection",
                "qualifiedName": f"default/api/{_conn_name}",
                "name": _conn_name,
            },
            "spec_url": spec_url,
            "load_to_atlan": True,
            "atlan_credential": {
                "name": "atlan",
                "credential_type": "atlan_api_token",
                "store_name": "default",
            },
            "loader_dry_run": True,
        }

        base_url = mgr.get_handler_url(OPENAPI_LOCAL.name)
        wf_result, _workflow_id, _ = await run_workflow_local(base_url, connector_input)

        result.assets_extracted = wf_result.get("total_scanned", 0)
        result.dry_run_validated = wf_result.get("atlan_validated_count", 0)
        result.dry_run_error_count = wf_result.get("atlan_error_count", 0)
        result.dry_run_errors = wf_result.get("atlan_errors", [])

        if result.assets_extracted > 0 and result.dry_run_validated > 0:
            result.status = "PASS"
        else:
            result.status = "FAIL"
            result.errors.append(
                f"Dry-run validation failed: "
                f"assets_extracted={result.assets_extracted}, "
                f"validated={result.dry_run_validated}, "
                f"errors={result.dry_run_error_count}"
            )

    except Exception as e:
        result.status = "FAIL"
        result.errors.append(str(e))
        import traceback

        traceback.print_exc()
    finally:
        mgr.stop_app(OPENAPI_LOCAL.name)
        result.duration_s = time.monotonic() - t0

    return result


# =============================================================================
# Test registry
# =============================================================================

_TEST_REGISTRY: list[tuple[str, str, Any]] = [
    ("openapi", "OpenAPI (dry-run)", run_openapi_test),
]


# =============================================================================
# Runner
# =============================================================================


def _format_result_text(results: list[LocalTestResult]) -> str:
    lines = [
        "",
        "=" * 70,
        "LOCAL REGRESSION TEST RESULTS",
        "=" * 70,
    ]
    for r in results:
        icon = {"PASS": "✓", "FAIL": "✗", "SKIPPED": "-", "PENDING": "?"}.get(
            r.status, "?"
        )
        lines.append(f"  {icon} {r.name:<20} {r.status:<10} {r.duration_s:.1f}s")
        if r.assets_extracted:
            lines.append(
                f"      assets_extracted={r.assets_extracted} validated={r.dry_run_validated}"
            )
        for err in r.errors:
            lines.append(f"      ERROR: {err}")

    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIPPED")
    lines.extend(
        [
            "",
            f"  Total: {len(results)}  Passed: {passed}  Failed: {failed}  Skipped: {skipped}",
            "=" * 70,
        ]
    )
    return "\n".join(lines)


def _format_result_json(results: list[LocalTestResult]) -> str:
    return json.dumps(
        [
            {
                "name": r.name,
                "status": r.status,
                "duration_s": r.duration_s,
                "assets_extracted": r.assets_extracted,
                "dry_run_validated": r.dry_run_validated,
                "dry_run_error_count": r.dry_run_error_count,
                "errors": r.errors,
            }
            for r in results
        ],
        indent=2,
    )


async def _run_all(selected: list[str] | None) -> list[LocalTestResult]:
    """Run all (or selected) regression tests."""
    to_run = [
        (short, label, fn)
        for short, label, fn in _TEST_REGISTRY
        if selected is None or short in selected
    ]

    secrets = _build_dapr_secrets()
    tmp_dir = prepare_dapr_components(secrets)
    components_path = os.path.join(tmp_dir, "components")
    results: list[LocalTestResult] = []

    try:
        # Start shared loader worker (needed for dry-run validation)
        mgr = LocalProcessManager(components_path)

        if secrets.get("atlan"):
            print("  Starting atlan-loader worker...")
            mgr.start_app(LOADER_LOCAL, handler=False)

        for short, label, fn in to_run:
            print(f"\n--- {label} ---")
            result = await fn(mgr)
            results.append(result)

        mgr.stop_all()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return results


async def main() -> int:
    """Main entry point."""
    sys.stdout.reconfigure(line_buffering=True)

    available = [short for short, _, _ in _TEST_REGISTRY]
    parser = create_local_argument_parser(
        "Run OpenAPI connector local dry-run regression tests",
        available_tests=available,
    )
    args = parser.parse_args()

    selected: list[str] | None = None
    if hasattr(args, "tests") and args.tests:
        selected = [t.strip() for t in args.tests.split(",")]

    results = await _run_all(selected)

    if args.format == "json":
        output_text = _format_result_json(results)
    else:
        output_text = _format_result_text(results)

    if args.output:
        from pathlib import Path

        Path(args.output).write_text(output_text)
        print(f"\nResults written to {args.output}")
    else:
        print(output_text)

    failed = sum(1 for r in results if r.status == "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
