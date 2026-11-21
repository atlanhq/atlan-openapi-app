"""Full-DAG e2e: a re-run on CREATE must not replay saved connection admins.

Regression suite for CONAT-6701 / CONNECT-3723. A CREATE workflow stores the
connection snapshot it was set up with, admin lists included, and hands it to
every run. ``map_connection`` used to re-emit that snapshot on the per-run
Connection upsert, so once a saved admin group was deleted from the tenant,
Atlas rejected the upsert (``ATLAS-400-00-029`` "Keycloak group not found")
and the run failed even though every API asset published.

Scenario:

1. Seed the connection out-of-band with the harness's valid admin ACL, so the
   run under test is a *re-run* against an existing connection (the incident
   shape) rather than a first-run create.
2. Run ONE CREATE DAG whose ``{{connection}}`` snapshot carries an admin group
   that does not exist on the tenant.
3. Assert every DAG node succeeded (on the pre-fix code the publish node fails
   with ``ATLAS-400-00-029``), and that the live connection's admins are exactly
   what was seeded: the stale group never landed and the omitted attributes did
   not clear the existing admins.

Independent of the other suites: its own ``connection_name_prefix`` (own AE
workflow slug) and, in CI, its own matrix leg with a dedicated worker + queue.

Requires ATLAN_BASE_URL + ATLAN_API_KEY. The module-level guard skips the test
when those env vars are absent, so it never runs accidentally in local or CI
unit-test invocations.
"""

from __future__ import annotations

import logging
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
    from application_sdk.contracts.types import ConnectionRef  # noqa: E402
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
# info.title of petstore3.json — the APISpec this run publishes. The seed probe
# writes the same asset, so the full-diff CREATE publish keeps it rather than
# archiving a foreign canary.
_PETSTORE_SPEC_TITLE = "Swagger Petstore - OpenAPI 3.0"


@pytest.mark.e2e
class TestConnectionCreateStaleAdmins(OpenapiGeneratedE2EBase):
    # Own AE workflow slug, so this suite never shares versioned state with
    # test_connection_create / test_connection_reuse.
    connection_name_prefix = "stale-admin-e2e-full-ci"

    mode = RunMode.AGENT

    expected_min_asset_counts = {"APISpec": 1, "APIPath": 13}
    expect_lineage = False

    ae_poll_interval_seconds = 30
    ae_poll_timeout_seconds = 1800
    atlas_poll_interval_seconds = 30
    atlas_poll_timeout_seconds = 900

    @property
    def _stale_admin_group(self) -> str:
        """A group name that cannot exist on the tenant (per-run unique)."""
        return f"e2e-deleted-group-{self.run_id}"

    def _mustache_substitutions(self) -> OpenapiMustacheSubstitutions:
        base = super()._mustache_substitutions()
        # The saved workflow snapshot: the harness's valid admins plus a group
        # that was deleted after setup — the incident's input shape.
        assert base.connection is not None
        attributes = base.connection.model_dump(by_alias=True)["attributes"]
        attributes["adminGroups"] = [self._stale_admin_group]
        return OpenapiMustacheSubstitutions(
            connection=ConnectionRef.model_validate(
                {"typeName": "Connection", "attributes": attributes}
            ),
            credential=base.credential,
            spec_url=_PETSTORE_URL,
            connection_usage="CREATE",
        )

    # _credential_body() inherits BaseE2ETest default of None — Petstore is a
    # public URL that needs no authentication.

    def _atlan_client(self):  # type: ignore[no-untyped-def]
        # pyatlan is a heavy, testing-time-only import (mirrors the harness).
        from pyatlan.client.atlan import AtlanClient  # noqa: PLC0415

        return AtlanClient(
            base_url=os.environ["ATLAN_BASE_URL"],
            api_key=os.environ["ATLAN_API_KEY"],
        )

    def _live_admins(self) -> dict[str, set[str]]:
        from pyatlan.model.assets import Connection  # noqa: PLC0415

        conn = self._atlan_client().asset.get_by_qualified_name(
            qualified_name=self.connection_qualified_name,
            asset_type=Connection,
            attributes=["adminUsers", "adminGroups", "adminRoles"],
        )
        return {
            "adminUsers": set(conn.admin_users or ()),
            "adminGroups": set(conn.admin_groups or ()),
            "adminRoles": set(conn.admin_roles or ()),
        }

    def _seed_existing_connection(self) -> dict[str, set[str]]:
        """Create the connection with valid admins; return its live admin ACL.

        The probe writes Petstore's APISpec and is retried until it succeeds,
        which gates the run on the seeded connection's policies being live.
        """
        from pyatlan.model.assets import APISpec  # noqa: PLC0415

        client = self._atlan_client()

        def _probe() -> None:
            client.asset.save(
                APISpec.creator(
                    name=_PETSTORE_SPEC_TITLE,
                    connection_qualified_name=self.connection_qualified_name,
                )
            )

        self.seed_connection(probe=_probe)
        seeded = self._live_admins()
        logger.info(
            "seeded connection %s with admins %s",
            self.connection_qualified_name,
            seeded,
        )
        assert self._stale_admin_group not in seeded["adminGroups"]
        assert seeded["adminUsers"] or seeded["adminRoles"], (
            f"seeded connection has no admins to preserve: {seeded}"
        )
        return seeded

    def test_full_dag_runs_end_to_end(self) -> None:
        """A CREATE re-run with a stale saved admin group publishes cleanly and
        leaves the live connection's admins untouched.

        Overrides the base single-run scenario: the connection must already
        exist (seeded with valid admins) so the run under test is a re-run whose
        saved snapshot names a group the tenant no longer has.
        """
        seeded = self._seed_existing_connection()

        outcome = self.run_full_dag()
        assert outcome.ae_result.all_nodes_succeeded, (
            "CREATE re-run failed with a stale admin group in the saved "
            "connection snapshot — the per-run Connection upsert is replaying "
            "saved admins (expect ATLAS-400-00-029 in publish). Failed nodes: "
            f"{[n.name for n in outcome.ae_result.failed_nodes]}"
        )
        assert outcome.connection_in_atlas, "seeded connection not found in Atlas"
        assert outcome.asset_counts.get("APISpec", 0) >= 1, outcome.asset_counts
        assert outcome.asset_counts.get("APIPath", 0) >= 13, outcome.asset_counts

        live = self._live_admins()
        assert self._stale_admin_group not in live["adminGroups"], (
            f"stale saved admin group landed on the live connection: {live}"
        )
        # Omitting the admin attributes must leave the existing ACL as seeded,
        # not clear it.
        assert live == seeded, (
            f"live connection admins changed by the run: seeded={seeded} live={live}"
        )
