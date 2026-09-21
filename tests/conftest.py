"""Root test conftest — environment that must be set before any SDK import.

``application_sdk.constants`` snapshots ``ATLAN_APPLICATION_NAME`` the first
time ``application_sdk`` is imported anywhere in the process, and the SDK's
integration fixture kit refuses to load when the live env var disagrees with
that snapshot (``IntegrationEnvOrderingError``). Setting it inside
``tests/integration/conftest.py`` is too late for a whole-tree run: pytest
imports ``tests/unit/conftest.py`` first, that pulls in ``application_sdk``,
and the snapshot is taken with the default name before the integration
conftest ever executes.

pytest loads conftest files from the rootdir down, so THIS file runs before
either suite's conftest — including when only ``tests/integration`` is
selected. It is therefore the single owner of these two variables; do not
re-set them further down the tree.

This is load-bearing for conformance F016: `detect --with-tests` runs the
whole `tests/` tree in one pytest process, and a collection error there exits
2, which the behaviour runner reports as `execution: error` — no scenario can
establish conformance while that is true.
"""

from __future__ import annotations

import os

os.environ.setdefault("ATLAN_APPLICATION_NAME", "openapi")
os.environ.setdefault("ATLAN_DEPLOYMENT_NAME", "ci")
