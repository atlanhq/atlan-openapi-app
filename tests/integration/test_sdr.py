"""SDR (Self-Deployed Runtime) integration test for the OpenAPI Spec Loader.

`atlan.yaml` declares `self_deployed_runtime: true`, so a
`BaseSDRIntegrationTest` subclass must exist to validate that the committed
manifest wires the SDR-specific inputs (in particular `agent_json`) needed
for agent-mode credential resolution — see DISTR-752 / atlan-mssql-app#177,
where a missing `agent_json` slot let the workflow report success while
extracting zero assets.

`scenarios` is intentionally left empty: exercising this class against a
real SDR compose stack requires an `E2E_OPENAPI_*` credential/spec-URL
fixture and CI wiring this repo does not have yet. `manifest_path` alone
still gives static/manual verification that the manifest's extract-args
shape is agent-routable.
"""

from __future__ import annotations

from application_sdk.testing.sdr.base import BaseSDRIntegrationTest


class TestOpenAPISDR(BaseSDRIntegrationTest):
    manifest_path = "app/generated/manifest.json"
