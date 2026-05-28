"""Full-DAG e2e test for the OpenAPI connector.

Submits a real AE workflow to a test tenant, runs extract → publish DAG
against the public Petstore v3 API, and asserts the resulting APISpec /
APIPath / APIObject assets land in Atlas.

Requires ATLAN_BASE_URL + ATLAN_API_KEY. The module-level guard skips
the test when those env vars are absent, so it never runs accidentally
in local or CI unit-test invocations. In CI, enabled by the ``e2e`` PR label.
"""

from __future__ import annotations

import os
import time

import pytest

if not os.environ.get("ATLAN_BASE_URL") or not os.environ.get("ATLAN_API_KEY"):
    pytest.skip(
        "e2e harness needs ATLAN_BASE_URL + ATLAN_API_KEY",
        allow_module_level=True,
    )

# Guard SDK version — BaseE2ETest was added after SDK 3.13.4. When the
# installed SDK is older the test is cleanly skipped rather than erroring.
try:
    from application_sdk.testing.e2e import RunMode  # noqa: E402
    from application_sdk.testing.e2e.payload import AgentSpec, ConnectionSpec  # noqa: E402
    from app.generated._e2e_base import OpenAPIGeneratedE2EBase  # noqa: E402
    from app.generated._e2e_substitutions import OpenAPIMustacheSubstitutions  # noqa: E402
    from pyatlan.model.enums import AtlanConnectorType  # noqa: E402
except ImportError as _exc:
    pytest.skip(
        f"SDK does not yet export agnostic e2e harness: {_exc}", allow_module_level=True
    )

# AtlanConnectorType.API.value == "api" — the segment Atlan uses in
# connection qualifiedNames for API Spec sources (not "openapi").
_CONNECTOR_TYPE = AtlanConnectorType.API.value


@pytest.mark.e2e
class TestOpenAPIE2E(OpenAPIGeneratedE2EBase):
    # Name-derived attrs (connector_short_name, argo_package_name,
    # argo_template_name, app_service_url) come from OpenAPIGeneratedE2EBase.

    mode = RunMode.AGENT

    # Petstore v3 spec: 1 APISpec, ≥ 20 APIPaths, ≥ 10 APIObjects.
    # Floors set conservatively to absorb transient Atlas indexing lag.
    expected_min_asset_counts = {"APISpec": 1, "APIPath": 20, "APIObject": 10}
    expect_lineage = False

    ae_poll_interval_seconds = 30
    ae_poll_timeout_seconds = 1800
    atlas_poll_interval_seconds = 30
    atlas_poll_timeout_seconds = 900

    def __init__(self) -> None:
        super().__init__()
        # OpenAPI connections in Atlan live under default/api/{epoch}, not
        # default/openapi/... The epoch (pure digits, unix seconds) is the
        # connection name — Atlas rejects names with hyphens/letters for
        # this connector type.
        _epoch = int(time.time())
        self.connection_qualified_name = f"default/{_CONNECTOR_TYPE}/{_epoch}"
        self.connection_display_name = f"{_CONNECTOR_TYPE}-{_epoch}"

    def connection_spec(self) -> ConnectionSpec:
        return ConnectionSpec(
            name=self.connection_display_name,
            qualified_name=self.connection_qualified_name,
            connector_name=_CONNECTOR_TYPE,
            source_logo="https://assets.atlan.com/assets/openapi.png",
            admin_users=self.connection_admin_users,
            admin_groups=self.connection_admin_groups,
            admin_roles=self.connection_admin_roles,
        )

    def agent_spec(self) -> AgentSpec:
        return AgentSpec(agent_name=f"openapi-e2e-ci-{self.run_id}")

    def _mustache_substitutions(self) -> OpenAPIMustacheSubstitutions:
        base = super()._mustache_substitutions()
        return OpenAPIMustacheSubstitutions(
            connection=base.connection,
            credential=base.credential,
            spec_url="https://petstore3.swagger.io/api/v3/openapi.json",
            # import_type defaults to "URL"; spec_prefix / spec_key /
            # cloud_source unused for direct-URL imports.
        )

    # _credential_body() inherits BaseE2ETest default of None —
    # Petstore is a public API that needs no authentication.
