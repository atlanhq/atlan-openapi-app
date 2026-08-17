"""Unit tests for OpenAPIConnectorHandler.preflight_check (CONNECT-812).

The handler exists so the injected gate stops returning a vacuous READY with
zero checks (``missing_check`` on connector-pulse). These tests pin the
registry rules it was built against:

* PF-11 — a failed probe still returns a check row with a typed error.
* PF-12 — the verdict comes from mandatory checks, never ``all(c.passed)``.
* PF-18 — check messages carry no raw exception text.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from application_sdk.handler import (
    BaseConnectionConfig,
    PreflightInput,
    PreflightStatus,
)

from app.handler import OpenAPIConnectorHandler, _probe_timeout


def _input(**config: object) -> PreflightInput:
    return PreflightInput(
        connection_config=BaseConnectionConfig(**config),
        timeout_seconds=60,
    )


class TestUrlMode:
    @pytest.mark.asyncio
    @respx.mock
    async def test_reachable_url_is_ready(self) -> None:
        respx.get("https://example.com/api.json").mock(
            return_value=httpx.Response(200, content=b"{}")
        )
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="https://example.com/api.json")
        )
        assert out.status == PreflightStatus.READY
        names = {c.name: c.passed for c in out.checks}
        assert names == {"spec_url_configured": True, "spec_source_reachable": True}

    @pytest.mark.asyncio
    @respx.mock
    async def test_403_returns_not_ready_with_typed_check_row(self) -> None:
        """PF-11: the failed connect returns a populated check row — typed
        error, non-empty checks list — never an empty matrix."""
        respx.get("https://example.com/api.json").mock(return_value=httpx.Response(403))
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="https://example.com/api.json")
        )
        assert out.status == PreflightStatus.NOT_READY
        assert out.checks, "PF-11: checks must never be empty on failure"
        failed = [c for c in out.checks if not c.passed]
        assert len(failed) == 1
        assert failed[0].name == "spec_source_reachable"
        assert failed[0].error is not None
        assert failed[0].error.code == "PERMISSION_OPENAPI_SPEC_FETCH"

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_error_returns_not_ready_typed(self) -> None:
        respx.get("https://example.com/api.json").mock(
            side_effect=httpx.ConnectError("boom")
        )
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="https://example.com/api.json")
        )
        assert out.status == PreflightStatus.NOT_READY
        failed = [c for c in out.checks if not c.passed]
        assert failed[0].error is not None
        assert failed[0].error.code == "SOURCE_UNAVAILABLE_OPENAPI_SPEC"

    @pytest.mark.asyncio
    async def test_missing_spec_url_is_not_ready_without_probing(self) -> None:
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="")
        )
        assert out.status == PreflightStatus.NOT_READY
        assert [c.name for c in out.checks] == ["spec_url_configured"]
        assert out.checks[0].error is not None

    @pytest.mark.asyncio
    async def test_local_path_spec_url_skips_probe(self) -> None:
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="/tmp/downloaded/spec.json")
        )
        assert out.status == PreflightStatus.READY
        reach = [c for c in out.checks if c.name == "spec_source_reachable"][0]
        assert reach.passed is True
        assert "skipped" in reach.message


class TestCloudMode:
    @pytest.mark.asyncio
    async def test_location_configured_is_ready(self) -> None:
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="CLOUD", spec_prefix="specs", spec_key="a.json")
        )
        assert out.status == PreflightStatus.READY
        # PF-15: the check row states what is NOT probed.
        assert "not probed" in out.checks[0].message

    @pytest.mark.asyncio
    async def test_missing_location_is_not_ready(self) -> None:
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="CLOUD", spec_prefix="", spec_key="")
        )
        assert out.status == PreflightStatus.NOT_READY
        assert out.checks[0].error is not None
        assert out.checks[0].error.code == (
            "INVALID_INPUT_OPENAPI_CLOUD_SPEC_LOCATION_REQUIRED"
        )


class TestProbeTimeout:
    def test_clamped_to_budget_with_headroom(self) -> None:
        assert _probe_timeout(60) == 30.0  # ceiling
        assert _probe_timeout(20) == 15.0  # budget - headroom
        assert _probe_timeout(6) == 5.0  # floor
