"""Per-test timeout for the tenant-hitting e2e suite.

``pyproject.toml`` sets ``--timeout=300`` as a hang guard sized for the unit and
integration tiers, which run in-process. A full-DAG e2e run does not fit in it:
each suite declares its own budgets — ``worker_health_timeout_seconds``,
``ae_poll_timeout_seconds``, ``atlas_poll_timeout_seconds`` — totalling ~47
minutes, so under the global cap those polls are structurally unreachable and a
slow tenant fails the test long before its own budget expires.

``.github/workflows/e2e-full-reusable.yaml`` in application-sdk already states
the rule for the GH job timeout: it "must always be > ae_poll_timeout_seconds +
atlas_poll_timeout_seconds + ~10 min build/setup overhead". This applies the
same rule one layer down, derived from each suite's own attributes so the bound
cannot drift when a budget changes.

Keeping a pytest-level bound — rather than dropping the timeout and leaning on
the 120-minute job timeout — is deliberate. pytest-timeout's ``signal`` method
raises inside the test, so ``teardown_method`` still runs and still purges the
connection the run created. A job-timeout kill skips teardown and leaks e2e
assets onto the tenant.
"""

from __future__ import annotations

from typing import Any

import pytest

# Covers everything in the test body that is not one of the declared poll
# budgets: connection/role resolution, AE workflow and seed-version creation,
# the /workflow/start call, the Atlas assertions, and the asset purge. Measured
# at 15-45s across CI runs, so this is generous on purpose — the point of the
# bound is to catch a wedge, not to police normal variance.
SETUP_AND_TEARDOWN_SLACK_SECONDS = 300

_BUDGET_ATTRS = (
    "worker_health_timeout_seconds",
    "ae_poll_timeout_seconds",
    "atlas_poll_timeout_seconds",
)


def e2e_timeout_seconds(cls: type) -> int | None:
    """Per-test timeout for an e2e suite, or None if it declares no budgets.

    Returning None leaves the item on whatever the global config gives it,
    which is the right answer for an e2e-marked test that is not a
    ``BaseE2ETest`` subclass and therefore does no tenant polling.
    """
    declared = [getattr(cls, name, None) for name in _BUDGET_ATTRS]
    budgets = [value for value in declared if value is not None]
    if not budgets:
        return None
    return sum(budgets) + SETUP_AND_TEARDOWN_SLACK_SECONDS


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Swap the global hang guard on e2e items for a budget-derived bound.

    A ``timeout`` marker takes precedence over ``--timeout`` in pytest-timeout,
    while ``--timeout-method=signal`` still applies (see ``_get_item_settings``).
    """
    for item in items:
        if item.get_closest_marker("e2e") is None:
            continue
        cls = getattr(item, "cls", None)
        if cls is None:
            continue
        timeout = e2e_timeout_seconds(cls)
        if timeout is not None:
            item.add_marker(pytest.mark.timeout(timeout))
