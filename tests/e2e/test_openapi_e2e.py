"""Full-DAG e2e test for the OpenAPI connector.

Submits a real AE workflow to a test tenant, runs extract → publish DAG
against the public Petstore v3 API, and asserts the resulting APISpec /
APIPath assets land in Atlas.

Requires ATLAN_BASE_URL + ATLAN_API_KEY. The module-level guard skips
the test when those env vars are absent, so it never runs accidentally
in local or CI unit-test invocations. In CI, enabled by the ``e2e`` PR label.
"""

from __future__ import annotations

import os

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
    from app.generated._e2e_base import OpenapiGeneratedE2EBase  # noqa: E402
    from app.generated._e2e_substitutions import OpenapiMustacheSubstitutions  # noqa: E402
except ImportError as _exc:
    pytest.skip(
        f"SDK does not yet export agnostic e2e harness: {_exc}", allow_module_level=True
    )


@pytest.mark.e2e
class TestOpenAPIE2E(OpenapiGeneratedE2EBase):
    # Name-derived attrs (connector_short_name, connection_type,
    # argo_package_name, argo_template_name, app_service_url) come from
    # OpenapiGeneratedE2EBase. The base harness builds the connection QN
    # as default/{connection_type}/{epoch} automatically.

    mode = RunMode.AGENT

    expected_min_asset_counts = {"APISpec": 1, "APIPath": 13}
    expect_lineage = False

    ae_poll_interval_seconds = 30
    ae_poll_timeout_seconds = 1800
    atlas_poll_interval_seconds = 30
    atlas_poll_timeout_seconds = 900

    # NOTE: no agent_spec() override. BaseE2ETest.agent_spec derives the agent
    # identity from the worker's own deployment env
    # (atlan-{ATLAN_APPLICATION_NAME}-{ATLAN_DEPLOYMENT_NAME}), so it picks up
    # the sdr-e2e per-leg ATLAN_DEPLOYMENT_NAME automatically and always matches
    # the queue the worker polls. Hard-coding a run-id-keyed name here would pin
    # the harness to the un-suffixed queue and desync it from the worker once the
    # overlay inherits the per-leg value — see conformance T017. Local runs fall
    # back to {connector}-{connection_name_prefix}-{run_id} via the base.

    def _mustache_substitutions(self) -> OpenapiMustacheSubstitutions:
        base = super()._mustache_substitutions()
        return OpenapiMustacheSubstitutions(
            connection=base.connection,
            credential=base.credential,
            spec_url="https://raw.githubusercontent.com/atlanhq/atlan-openapi-app/main/tests/integration/petstore3.json",
            # import_type defaults to "URL"; spec_prefix / spec_key /
            # cloud_source unused for direct-URL imports.
        )

    # _credential_body() inherits BaseE2ETest default of None —
    # Petstore is a public API that needs no authentication.
