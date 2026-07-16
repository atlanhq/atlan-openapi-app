"""Full-DAG e2e test for the OpenAPI connector's REUSE / assertion-only path.

With ``connection_usage="REUSE"`` the connector targets an *existing* connection
(selected via ``connection_qualified_name``), does NOT re-emit the Connection
entity, and emits ``assertion_only_enabled=True``. The publish node reads that
via ``$.extract.outputs.assertion_only_enabled`` and runs atlan-publish-app in
assertion-only mode: the transformed rows are forwarded as pure upserts with NO
diff and NO archival (see the publish-app assertion-only publish contract).

Design — seed out-of-band, single assertion-only run:

REUSE needs a pre-existing connection. Rather than establish it with a CREATE
*publish* (which chains a flaky connection-policy-propagation race in front of
the thing under test — a fresh connection 403s child writes until its access
policies go live), this suite seeds the connection **and a foreign canary
asset** directly via pyatlan, waits until writes succeed, then runs ONE
REUSE/assertion-only DAG for Petstore. Because assertion-only never archives,
the canary must survive — a normal full-diff publish would archive it (it isn't
in Petstore's extraction). CREATE-mode publish is covered by
``test_connection_create``.

This suite is independent of ``test_connection_create`` (its own seeded
connection and its own AE workflow slug), and the matrix runs each suite as its
own leg with a dedicated worker + Temporal queue, so the two run concurrently
without sharing state.

Requires ATLAN_BASE_URL + ATLAN_API_KEY. The module-level guard skips the test
when those env vars are absent, so it never runs accidentally in local or CI
unit-test invocations. In CI, enabled by the ``e2e`` PR label.
"""

from __future__ import annotations

import logging
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
    from app.generated._e2e_base import OpenapiGeneratedE2EBase  # noqa: E402
    from app.generated._e2e_substitutions import OpenapiMustacheSubstitutions  # noqa: E402
except ImportError as _exc:
    pytest.skip(
        f"SDK does not yet export agnostic e2e harness: {_exc}", allow_module_level=True
    )

logger = logging.getLogger(__name__)

_PETSTORE_URL = (
    "https://raw.githubusercontent.com/atlanhq/atlan-openapi-app/main"
    "/tests/integration/petstore3.json"
)


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
class TestConnectionReuse(OpenapiGeneratedE2EBase):
    # Name-derived attrs come from OpenapiGeneratedE2EBase. agent_spec() is
    # inherited: the base harness derives the worker queue from
    # ATLAN_APPLICATION_NAME + ATLAN_DEPLOYMENT_NAME, and the matrix gives this
    # leg its own per-leg ATLAN_DEPLOYMENT_NAME (hence its own worker + queue).
    # A distinct connection_name_prefix additionally gives this suite its OWN AE
    # workflow slug, so it never shares versioned state with test_connection_create.
    connection_name_prefix = "reuse-e2e-full-ci"

    mode = RunMode.AGENT

    # Floors: Petstore (1 APISpec + 13 APIPaths) PLUS the seeded canary APISpec.
    # Requiring APISpec>=2 makes the count-poll wait until both the canary and
    # Petstore's spec are present — i.e. the canary survived the assertion-only
    # publish. A normal diff would have archived it, leaving APISpec==1.
    expected_min_asset_counts = {"APISpec": 2, "APIPath": 13}
    expect_lineage = False

    ae_poll_interval_seconds = 30
    ae_poll_timeout_seconds = 1800
    atlas_poll_interval_seconds = 30
    atlas_poll_timeout_seconds = 900
    # inherits ae_stall_grace_seconds = 180 (dedicated per-leg CI worker).

    def _mustache_substitutions(self) -> _ReuseSubstitutions:
        base = super()._mustache_substitutions()
        return _ReuseSubstitutions(
            connection=base.connection,
            credential=base.credential,
            spec_url=_PETSTORE_URL,
            # The whole point of this suite: REUSE => assertion-only publish,
            # targeting the connection we seed below.
            connection_usage="REUSE",
            connection_qualified_name=self.connection_qualified_name,
        )

    # _credential_body() inherits BaseE2ETest default of None — Petstore is a
    # public URL that needs no authentication.

    def test_full_dag_runs_end_to_end(self) -> None:
        """Override the base single-run scenario.

        A bare REUSE run has no connection to reuse (assertion-only never creates
        one). The end-to-end REUSE proof lives in
        ``test_reuse_assertion_only_publishes_without_archiving``, which seeds the
        connection first.
        """
        pytest.skip(
            "REUSE requires a pre-existing connection; covered by "
            "test_reuse_assertion_only_publishes_without_archiving"
        )

    def _seed_connection_and_canary(self) -> str:
        """Create the connection + a foreign canary APISpec via pyatlan and
        return the canary's qualified name.

        Seeding out-of-band (not via a CREATE publish) keeps the flaky
        connection-policy race out of the assertion-only run under test. The
        canary write is retried until it succeeds, which doubles as the gate that
        the connection's policies are live before the DAG runs.
        """
        # pyatlan is a heavy, testing-time-only import (mirrors the harness).
        from pyatlan.client.atlan import AtlanClient  # noqa: PLC0415
        from pyatlan.model.assets import APISpec, Connection  # noqa: PLC0415
        from pyatlan.model.enums import AtlanConnectorType  # noqa: PLC0415

        client = AtlanClient(
            base_url=os.environ["ATLAN_BASE_URL"],
            api_key=os.environ["ATLAN_API_KEY"],
        )

        admin_roles = list(
            self.connection_admin_roles or getattr(self, "_auto_admin_roles", ())
        )
        admin_users = list(
            self.connection_admin_users or getattr(self, "_auto_admin_users", ())
        )
        conn = Connection.creator(
            client=client,
            name=self.connection_display_name,
            connector_type=AtlanConnectorType.API,
            admin_roles=admin_roles or None,
            admin_users=admin_users or None,
        )
        client.asset.save(conn)
        # creator assigns the qualifiedName client-side; adopt it as the QN the
        # REUSE run, connection poll, and teardown all key off.
        self.connection_qualified_name = conn.qualified_name
        logger.info("seeded connection %s", self.connection_qualified_name)

        # Wait until the connection is searchable (policy provisioning starts).
        assert self.client.poll_atlas_for_connection(
            self.connection_qualified_name,
            interval_seconds=self.atlas_poll_interval_seconds,
            timeout_seconds=self.atlas_poll_timeout_seconds,
        ), "seeded connection never became searchable"

        # Write a foreign canary APISpec — a name Petstore never produces, so a
        # normal diff would archive it. Retry until it succeeds: a fresh
        # connection 403s child writes until its access policies are live.
        canary = APISpec.creator(
            name=f"connect55-canary-{self.run_id}",
            connection_qualified_name=self.connection_qualified_name,
        )
        deadline = time.monotonic() + 300
        while True:
            try:
                client.asset.save(canary)
                logger.info("seeded canary APISpec %s", canary.qualified_name)
                return canary.qualified_name
            except Exception:  # noqa: BLE001 — policies not live yet (403); retry
                if time.monotonic() >= deadline:
                    raise
                logger.info("canary write not yet permitted; retrying")
                time.sleep(self.atlas_poll_interval_seconds)

    def test_reuse_assertion_only_publishes_without_archiving(self) -> None:
        """Assertion-only REUSE into a seeded connection must not archive the
        connection's pre-existing (foreign) assets.

        Seed connection C + a canary APISpec via pyatlan, then run ONE
        REUSE/assertion-only DAG for Petstore into C. Because assertion-only
        skips the diff, the canary survives and coexists with Petstore's assets
        (APISpec==2). A normal full-diff run would archive the canary
        (APISpec==1), since it isn't in Petstore's extraction.
        """
        canary_qn = self._seed_connection_and_canary()

        outcome = self.run_full_dag()
        assert outcome.ae_result.all_nodes_succeeded, (
            "REUSE/assertion-only DAG nodes did not all succeed: "
            f"{[n.name for n in outcome.ae_result.failed_nodes]}"
        )
        assert outcome.connection_in_atlas, "seeded connection not found in Atlas"

        # Petstore landed through the assertion-only publish path.
        assert outcome.asset_counts.get("APIPath", 0) >= 13, (
            f"Petstore APIPaths did not land: {outcome.asset_counts}"
        )
        # The no-delete signal: the canary APISpec and Petstore's APISpec both
        # exist. A normal diff would have archived the canary, leaving only one.
        assert outcome.asset_counts.get("APISpec", 0) >= 2, (
            "Assertion-only re-publish archived the pre-existing canary APISpec "
            f"({canary_qn}) — expected it to coexist with Petstore's spec, but "
            f"only found {outcome.asset_counts.get('APISpec', 0)} APISpec(s). "
            "A full-diff run would drop the canary; assertion-only must not."
        )
