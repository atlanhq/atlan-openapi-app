"""Unit tests for the derived e2e per-test timeout.

Guards the invariant that tests/e2e/conftest.py exists to enforce: the per-test
timeout must cover every poll budget an e2e suite declares, and must still land
below the GH job timeout so it fires first and lets teardown purge the tenant.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e.conftest import (
    SETUP_AND_TEARDOWN_SLACK_SECONDS,
    e2e_timeout_seconds,
    pytest_collection_modifyitems,
)

# .github/workflows/e2e-full-reusable.yaml (application-sdk) defaults the e2e
# job to 120 minutes. A derived timeout at or above this never fires.
JOB_TIMEOUT_SECONDS = 120 * 60


class _AppSuite:
    """Budgets as declared by tests/e2e/test_connection_{create,reuse}.py."""

    worker_health_timeout_seconds = 120
    ae_poll_timeout_seconds = 1800
    atlas_poll_timeout_seconds = 900


class _SdkDefaultSuite:
    """Budgets as defaulted by application_sdk.testing.e2e.base.BaseE2ETest."""

    worker_health_timeout_seconds = 120
    ae_poll_timeout_seconds = 600
    atlas_poll_timeout_seconds = 1500


class _NoBudgets:
    """An e2e-marked class that is not a BaseE2ETest subclass."""


class _FakeItem:
    """Minimal stand-in for a collected pytest item."""

    def __init__(self, cls: type | None, markers: tuple[str, ...]) -> None:
        self.cls = cls
        self._markers = markers
        self.added: list[Any] = []

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name in self._markers else None

    def add_marker(self, marker: Any) -> None:
        self.added.append(marker)


# =============================================================================
# e2e_timeout_seconds
# =============================================================================


class TestE2ETimeoutSeconds:
    def test_sums_declared_budgets_plus_slack(self) -> None:
        """The timeout is every declared budget plus setup/teardown slack."""
        assert e2e_timeout_seconds(_AppSuite) == (
            120 + 1800 + 900 + SETUP_AND_TEARDOWN_SLACK_SECONDS
        )

    @pytest.mark.parametrize("suite", [_AppSuite, _SdkDefaultSuite])
    def test_exceeds_the_poll_budgets_it_must_cover(self, suite: type) -> None:
        """A poll that runs to its full budget must not trip the timeout.

        This is the bug the conftest fixes: under the global 300s cap the
        1800s AE poll was structurally unreachable.
        """
        timeout = e2e_timeout_seconds(suite)
        assert timeout is not None
        assert timeout > (
            suite.ae_poll_timeout_seconds + suite.atlas_poll_timeout_seconds
        )

    @pytest.mark.parametrize("suite", [_AppSuite, _SdkDefaultSuite])
    def test_stays_below_the_job_timeout(self, suite: type) -> None:
        """The pytest bound must fire before the job is killed.

        A job-timeout kill skips teardown_method, leaking the connection and
        its assets onto the tenant.
        """
        timeout = e2e_timeout_seconds(suite)
        assert timeout is not None
        assert timeout < JOB_TIMEOUT_SECONDS

    def test_returns_none_when_no_budgets_declared(self) -> None:
        """A non-harness class keeps whatever the global config gives it."""
        assert e2e_timeout_seconds(_NoBudgets) is None

    def test_ignores_non_integer_budgets(self) -> None:
        """A misdeclared budget is skipped rather than crashing collection."""

        class _Misdeclared:
            worker_health_timeout_seconds = 120
            ae_poll_timeout_seconds = "1800"

        assert e2e_timeout_seconds(_Misdeclared) == (
            120 + SETUP_AND_TEARDOWN_SLACK_SECONDS
        )


# =============================================================================
# pytest_collection_modifyitems
# =============================================================================


class TestCollectionHook:
    def test_marks_e2e_items(self) -> None:
        """An e2e item gets a timeout marker carrying the derived budget."""
        item = _FakeItem(_AppSuite, markers=("e2e",))

        pytest_collection_modifyitems(config=None, items=[item])

        assert len(item.added) == 1
        assert item.added[0].args == (e2e_timeout_seconds(_AppSuite),)

    def test_leaves_non_e2e_items_alone(self) -> None:
        """Unit and integration items keep the 300s hang guard."""
        item = _FakeItem(_AppSuite, markers=("integration",))

        pytest_collection_modifyitems(config=None, items=[item])

        assert item.added == []

    def test_skips_module_level_e2e_functions(self) -> None:
        """An e2e item with no class has no budgets to derive from."""
        item = _FakeItem(None, markers=("e2e",))

        pytest_collection_modifyitems(config=None, items=[item])

        assert item.added == []

    def test_skips_e2e_classes_without_budgets(self) -> None:
        """Never silently re-apply a bound the suite did not ask for."""
        item = _FakeItem(_NoBudgets, markers=("e2e",))

        pytest_collection_modifyitems(config=None, items=[item])

        assert item.added == []
