"""Full-DAG e2e test for the OpenAPI connector's REUSE / assertion-only path.

This is the connection-reuse counterpart to ``test_openapi_e2e.py``. With
``connection_usage="REUSE"`` the connector targets an *existing* connection
(selected via ``connection_qualified_name``), does NOT re-emit the Connection
entity, and emits ``assertion_only_enabled=True``. The publish node reads that
via ``$.extract.outputs.assertion_only_enabled`` and runs atlan-publish-app in
assertion-only mode: the transformed rows are forwarded as pure upserts with NO
diff and NO archival (see the publish-app assertion-only publish contract).

Because assertion-only never *creates* a connection, REUSE requires the
connection to already exist. The single scenario here therefore runs two DAGs
against one connection:

1. **CREATE + Petstore** — establishes the connection and 13 APIPaths via the
   normal full-diff publish path.
2. **REUSE + a disjoint secondary spec** — assertion-only publish into the SAME
   connection. This proves both that the assertion-only path lands assets all
   the way through publish AND — the behaviour we actually need — that the first
   spec's assets SURVIVE (a normal full-diff run would archive them, since
   they're absent from the second spec's extraction).

Requires ATLAN_BASE_URL + ATLAN_API_KEY. The module-level guard skips the test
when those env vars are absent, so it never runs accidentally in local or CI
unit-test invocations. In CI, enabled by the ``e2e`` PR label.
"""

from __future__ import annotations

import os
import time

import pytest
from pydantic import Field

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
        os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "main"
    )
    return (
        f"https://raw.githubusercontent.com/atlanhq/atlan-openapi-app/{ref}/{repo_path}"
    )


# Primary spec: the same Swagger Petstore v3 used by the CREATE-path e2e
# (13 APIPaths + 1 APISpec). Secondary spec: a small, DISJOINT spec (3 APIPaths
# + 1 APISpec, different title) so the two runs produce non-overlapping assets.
_PETSTORE_URL = _raw_url("tests/integration/petstore3.json")
_SECONDARY_URL = _raw_url("tests/e2e/openapi_secondary_spec.json")


class _ReuseSubstitutions(OpenapiMustacheSubstitutions):
    """Adds the ``connection_qualified_name`` mustache key.

    The contract-toolkit's e2e-substitutions generator does not emit a field for
    ``ConnectionSelector`` inputs, so the generated ``OpenapiMustacheSubstitutions``
    has no ``connection_qualified_name``. In production Heracles substitutes
    ``{{connection_qualified_name}}`` from the form; this subclass lets the e2e
    harness do the same so the REUSE run can name the connection to reuse.
    """

    connection_qualified_name: str = Field(
        default="", alias="{{connection_qualified_name}}"
    )


@pytest.mark.e2e
class TestOpenAPIReuseAssertionOnlyE2E(OpenapiGeneratedE2EBase):
    # Name-derived attrs (connector_short_name, connection_type,
    # argo_package_name, argo_template_name, app_service_url) come from
    # OpenapiGeneratedE2EBase. The base harness builds the connection QN as
    # default/{connection_type}/{epoch} once per test and reuses it across every
    # run_full_dag() call on this instance — which is exactly what the two-run
    # scenario needs (CREATE then REUSE into one connection).

    mode = RunMode.AGENT

    # Floors are the Petstore counts — the primary spec of run 1.
    expected_min_asset_counts = {"APISpec": 1, "APIPath": 13}
    expect_lineage = False

    ae_poll_interval_seconds = 30
    ae_poll_timeout_seconds = 1800
    # Opt in to the harness stall guard: this suite runs two DAGs against ONE
    # dedicated CI worker (docker-compose, not KEDA-autoscaled), so a queue with
    # no poller means a real agent-name/task-queue mismatch, not a busy worker.
    # Fail fast in ~3 min with an actionable message instead of hanging for the
    # full ae_poll_timeout_seconds (30 min). Safe here precisely because the
    # worker is dedicated; do NOT copy this onto suites that hit shared/
    # autoscaled infra where legitimate pickup can take much longer.
    ae_stall_grace_seconds = 180
    atlas_poll_interval_seconds = 30
    atlas_poll_timeout_seconds = 900

    # Per-run state read by _mustache_substitutions(); flipped between the two
    # run_full_dag() calls in the scenario below.
    _connection_usage: str = "CREATE"
    _spec_url: str = _PETSTORE_URL
    _reuse_qn: str = ""

    def agent_spec(self) -> AgentSpec:
        # The agent_name MUST resolve to the queue the single CI worker polls,
        # i.e. atlan-{ATLAN_APPLICATION_NAME}-{ATLAN_DEPLOYMENT_NAME} =
        # atlan-openapi-e2e-full-ci-<run_id> (see .github/e2e/
        # e2e-full-docker-compose.yaml). It is NOT a per-test identifier — every
        # e2e class in this connector shares the one worker, so this matches the
        # CREATE-path test's agent_name. Using a distinct name here would point
        # the DAG at a queue with no worker (the SDK harness now fails fast with
        # NoWorkerOnTaskQueueError instead of hanging).
        return AgentSpec(agent_name=f"openapi-e2e-full-ci-{self.run_id}")

    def _mustache_substitutions(self) -> _ReuseSubstitutions:
        base = super()._mustache_substitutions()
        return _ReuseSubstitutions(
            connection=base.connection,
            credential=base.credential,
            spec_url=self._spec_url,
            connection_usage=self._connection_usage,
            connection_qualified_name=self._reuse_qn,
        )

    # _credential_body() inherits BaseE2ETest default of None — the specs are
    # public URLs that need no authentication.

    def test_full_dag_runs_end_to_end(self) -> None:
        """Override the base single-run scenario.

        A bare REUSE run would have no connection to reuse (assertion-only never
        creates one). The end-to-end REUSE proof lives in
        ``test_reuse_assertion_only_publishes_without_archiving``, which
        establishes the connection with a CREATE run first.
        """
        pytest.skip(
            "REUSE requires a pre-existing connection; covered by "
            "test_reuse_assertion_only_publishes_without_archiving"
        )

    def _poll_asset_counts(
        self, *, min_api_paths: int, timeout_seconds: int = 120
    ) -> dict[str, int]:
        """Poll Atlas until at least ``min_api_paths`` APIPaths are indexed under
        the connection, or the timeout elapses. Returns the last counts seen.

        Used after the REUSE run so late-indexing secondary-spec assets are given
        time to appear before we assert on the combined total.
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

    def test_reuse_assertion_only_publishes_without_archiving(self) -> None:
        """CREATE then assertion-only REUSE into one connection: the first spec's
        assets must survive the second run.

        Run 1 (CREATE, Petstore, 13 paths) establishes the connection via the
        normal full-diff publish. Run 2 (REUSE, secondary spec, 3 disjoint paths)
        publishes assertion-only into the SAME connection. Because assertion-only
        skips the diff, NOTHING is archived: after Run 2 both specs' assets
        coexist. A normal full-diff Run 2 would instead archive all 13 Petstore
        paths (absent from the secondary spec).
        """
        # --- Run 1: CREATE + Petstore (establishes the connection) ---
        self._connection_usage = "CREATE"
        self._spec_url = _PETSTORE_URL
        self._reuse_qn = ""
        outcome1 = self.run_full_dag()
        assert outcome1.ae_result.all_nodes_succeeded, (
            "Run 1 (CREATE) DAG nodes did not all succeed: "
            f"{[n.name for n in outcome1.ae_result.failed_nodes]}"
        )
        assert outcome1.connection_in_atlas, "Run 1 connection not found in Atlas"
        base_specs = outcome1.asset_counts.get("APISpec", 0)
        base_paths = outcome1.asset_counts.get("APIPath", 0)
        assert base_specs >= 1, f"Run 1 APISpec count too low: {base_specs}"
        assert base_paths >= 13, f"Run 1 APIPath count too low: {base_paths}"

        # --- Run 2: REUSE + disjoint secondary spec, SAME connection ---
        self._connection_usage = "REUSE"
        self._spec_url = _SECONDARY_URL
        self._reuse_qn = self.connection_qualified_name
        outcome2 = self.run_full_dag()
        assert outcome2.ae_result.all_nodes_succeeded, (
            "Run 2 (REUSE/assertion-only) DAG nodes did not all succeed: "
            f"{[n.name for n in outcome2.ae_result.failed_nodes]}"
        )

        # After Run 2, both specs must coexist. The no-delete signal is that the
        # Petstore assets are STILL present; the secondary spec adds >= 1 more.
        final_counts = self._poll_asset_counts(min_api_paths=base_paths + 1)

        assert final_counts.get("APISpec", 0) >= base_specs + 1, (
            "Assertion-only re-publish archived the first spec's APISpec: "
            f"before={base_specs}, after={final_counts.get('APISpec', 0)} "
            "(expected both specs' APISpecs to coexist)"
        )
        assert final_counts.get("APIPath", 0) >= base_paths + 1, (
            "Assertion-only re-publish archived the first spec's APIPaths: "
            f"base_paths={base_paths}, "
            f"after_reuse_run={final_counts.get('APIPath', 0)} "
            "(a full-diff run would have dropped to just the secondary spec's "
            "paths — assertion-only must preserve all of them)"
        )
