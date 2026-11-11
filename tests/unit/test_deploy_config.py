"""Deployment-config tests for the rendered atlan.yaml.

CONNECT-4059: release 0.7.8 rendered ``deploy.dapr.secretstore: false`` (the
contract toolkit default for an empty ``DeployConfig``). The Dapr sidecar then
loaded no ``openapi-secretstore`` component, so every credential lookup in
``preflight`` and ``download_cloud_spec`` got HTTP 500 from
``/v1.0/secrets/openapi-secretstore/<guid>`` and the run failed. This app
resolves credentials (``hasCredentialConfig = true``), so it needs the secret
store, and it reads/writes files, so it needs the object store.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_ATLAN_YAML = Path(__file__).parents[2] / "atlan.yaml"


def _dapr_components() -> dict:
    return yaml.safe_load(_ATLAN_YAML.read_text())["deploy"]["dapr"]


def test_secretstore_component_enabled() -> None:
    assert _dapr_components()["secretstore"] is True, (
        "atlan.yaml deploy.dapr.secretstore must be true: credential lookups "
        "go through the openapi-secretstore Dapr component."
    )


def test_objectstore_component_enabled() -> None:
    assert _dapr_components()["objectstore"] is True
