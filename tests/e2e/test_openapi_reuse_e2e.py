"""Full-DAG e2e test for the OpenAPI connector's REUSE / assertion-only path.

This is the connection-reuse counterpart to ``test_openapi_e2e.py``. It exercises
``connection_usage="REUSE"``, which makes the connector emit
``assertion_only_enabled=True`` in its output. The publish node reads that via
``$.extract.outputs.assertion_only_enabled`` and runs atlan-publish-app in
assertion-only mode: the transformed rows are forwarded as pure upserts with NO
diff and NO archival (see the publish-app assertion-only publish contract).

Two scenarios:

1. ``test_full_dag_runs_end_to_end`` (inherited from the base harness) — proves
   the assertion-only path works all the way through the publish step: a REUSE
   run lands APISpec + APIPath assets in Atlas via the passthrough lane.

2. ``test_reuse_does_not_archive_preexisting_assets`` — the stronger proof of
   the behaviour we actually need. It publishes two *disjoint* specs into the
   SAME connection back-to-back in assertion-only mode and asserts the first
   spec's assets SURVIVE the second run. A normal full-diff publish would
   archive them (they're absent from the second spec's extraction); assertion-
   only mode must not. This is the regression guard for the connection-reuse
   clobbering problem.

Requires ATLAN_BASE_URL + ATLAN_API_KEY. The module-level guard skips the test
when those env vars are absent, so it never runs accidentally in local or CI
unit-test invocations. In CI, enabled by the ``e2e`` PR label.
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
    from application_sdk.testing.e2e.payload import AgentSpec  # noqa: E402
    from app.generated._e2e_base import OpenapiGeneratedE2EBase  # noqa: E402
    from app.generated._e2e_substitutions import OpenapiMustacheSubstitutions  # noqa: E402
except ImportError as _exc:
    pytest.skip(
        f"SDK does not yet export agnostic e2e harness: {_exc}", allow_module_level=True
    )


def _raw_url(repo_path: str) -> str:
    """Build a raw.githubusercontent URL for a file in this repo.

    Resolves against the current branch when running in CI (so a spec fixture
    added in the same PR is reachable during that PR's e2e run), falling back to
    ``main`` for post-merge / local runs.
    """
    ref = (
        os.environ.get("GITHUB_HEAD_REF")
        or os.environ.get("GITHUB_REF_NAME")
        or "main"
    )
    return f"https://raw.githubusercontent.com/atlanhq/atlan-openapi-app/{ref}/{repo_path}"


# Primary spec: the same Swagger Petstore v3 used by the CREATE-path e2e
# (13 APIPaths + 1 APISpec). Secondary spec: a small, DISJOINT spec (3 APIPaths
# + 1 APISpec, different title) so the two runs produce non-overlapping assets.
_PETSTORE_URL = _raw_url("tests/integration/petstore3.json")
_SECONDARY_URL = _raw_url("tests/e2e/openapi_secondary_spec.json")


@pytest.mark.e2e
class TestOpenAPIReuseAssertionOnlyE2E(OpenapiGeneratedE2EBase):
    # Name-derived attrs (connector_short_name, connection_type,
    # argo_package_name, argo_template_name, app_service_url) come from
    # OpenapiGeneratedE2EBase. The base harness builds the connection QN as
    # default/{connection_type}/{epoch} automatically and reuses it across every
    # run_full_dag() call on this instance — which is exactly what the no-delete
    # scenario needs (two runs, one connection).

    mode = RunMode.AGENT

    # Floors are the Petstore counts — the primary spec of both scenarios.
    expected_min_asset_counts = {"APISpec": 1, "APIPath": 13}
    expect_lineage = False

    ae_poll_interval_seconds = 30
    ae_poll_timeout_seconds = 1800
    atlas_poll_interval_seconds = 30
    atlas_poll_timeout_seconds = 900

    # The spec the next run_full_dag() will extract. Flipped between runs by the
    # no-delete scenario; defaults to Petstore for the inherited single-run test.
    _spec_url: str = _PETSTORE_URL

    def agent_spec(self) -> AgentSpec:
        return AgentSpec(agent_name=f"openapi-reuse-e2e-{self.run_id}")

    def _mustache_substitutions(self) -> OpenapiMustacheSubstitutions:
        base = super()._mustache_substitutions()
        return OpenapiMustacheSubstitutions(
            connection=base.connection,
            credential=base.credential,
            spec_url=self._spec_url,
            # The whole point of this suite: REUSE => assertion-only publish.
            connection_usage="REUSE",
        )

    # _credential_body() inherits BaseE2ETest default of None — the specs are
    # public URLs that need no authentication.

    def _poll_asset_counts(
        self, *, min_api_paths: int, timeout_seconds: int = 120
    ) -> dict[str, int]:
        """Poll Atlas until at least ``min_api_paths`` APIPaths are indexed under
        the connection, or the timeout elapses. Returns the last counts seen.

        Used after the second run so late-indexing secondary-spec assets are
        given time to appear before we assert on the combined total.
        """
        deadline = time.monotonic() + timeout_seconds
        counts: dict[str, int] = {}
        while True:
            counts = self.client.count_assets_under_connection(
                self.connection_qualified_name,
                type_names=("APISpec", "APIPath"),
            )
            if counts.get("APIPath", 0) >= min_api_paths:
                return counts
            if time.monotonic() >= deadline:
                return counts
            time.sleep(self.atlas_poll_interval_seconds)

    def test_reuse_does_not_archive_preexisting_assets(self) -> None:
        """Two disjoint assertion-only publishes into one connection: the first
        spec's assets must survive the second run.

        Run 1 (Petstore, 13 paths) then Run 2 (secondary, 3 disjoint paths),
        both connection_usage=REUSE => assertion-only. Because assertion-only
        skips the diff, NOTHING is archived: after Run 2 both specs' assets
        coexist under the connection. A normal full-diff Run 2 would instead
        archive all 13 Petstore paths (absent from the secondary spec).
        """
        # --- Run 1: Petstore under assertion-only ---
        self._spec_url = _PETSTORE_URL
        outcome1 = self.run_full_dag()
        assert outcome1.ae_result.all_nodes_succeeded, (
            f"Run 1 DAG nodes did not all succeed: "
            f"{[n.name for n in outcome1.ae_result.failed_nodes]}"
        )
        assert outcome1.connection_in_atlas, "Run 1 connection not found in Atlas"
        petstore_specs = outcome1.asset_counts.get("APISpec", 0)
        petstore_paths = outcome1.asset_counts.get("APIPath", 0)
        assert petstore_specs >= 1, f"Run 1 APISpec count too low: {petstore_specs}"
        assert petstore_paths >= 13, f"Run 1 APIPath count too low: {petstore_paths}"

        # --- Run 2: secondary (disjoint) spec, SAME connection, assertion-only ---
        self._spec_url = _SECONDARY_URL
        outcome2 = self.run_full_dag()
        assert outcome2.ae_result.all_nodes_succeeded, (
            f"Run 2 DAG nodes did not all succeed: "
            f"{[n.name for n in outcome2.ae_result.failed_nodes]}"
        )

        # After Run 2, both specs must coexist. The no-delete signal is that the
        # Petstore paths are STILL present; the secondary spec adds >= 1 more.
        final_counts = self._poll_asset_counts(min_api_paths=petstore_paths + 1)

        assert final_counts.get("APISpec", 0) >= petstore_specs + 1, (
            "Assertion-only re-publish archived the first spec's APISpec: "
            f"before={petstore_specs}, after={final_counts.get('APISpec', 0)} "
            "(expected both specs' APISpecs to coexist)"
        )
        assert final_counts.get("APIPath", 0) >= petstore_paths + 1, (
            "Assertion-only re-publish archived the first spec's APIPaths: "
            f"petstore_paths={petstore_paths}, "
            f"after_second_run={final_counts.get('APIPath', 0)} "
            "(a full-diff run would have dropped to just the secondary spec's "
            "paths — assertion-only must preserve all of them)"
        )
