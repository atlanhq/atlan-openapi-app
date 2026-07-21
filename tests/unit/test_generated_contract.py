"""Contract-tier tests for the generated UI config (app/generated/openapi.json).

These guard the connection *creation* path. When a user picks
connection_usage=CREATE, the frontend mints a brand-new connection whose
qualifiedName is ``default/{connector}/{epoch}``. The frontend's connection
(create) widget reads that connector segment from ``ui.connector`` — NOT from
the app id.

The bug this pins down: the app id is ``openapi`` but the connector type is
``api`` (Connectors.API). If the create-connection widget does not surface the
``api`` connector via ``ui.connector``, the frontend falls back to the app id
(the workflow's ``orchestration.atlan.com/source`` label) and mints
``default/openapi/{epoch}`` instead of the correct ``default/api/{epoch}`` —
poisoning the prefix of every child asset.

Note the two connection widgets read *different* ui keys (verified against the
frontend, dynamicForm2/widget/*.vue):
  * CREATE (ConnectionCreator, widget "connection")        → ``ui.connector``
  * REUSE  (ConnectionSelector, widget "connectionSelector") → ``ui.connectorName``
"""

from __future__ import annotations

import json
from pathlib import Path

_GENERATED_CONFIG = Path(__file__).parents[2] / "app" / "generated" / "openapi.json"

# The connection connector type must be the Connectors.API value, never the
# app id ("openapi").
_EXPECTED_CONNECTOR = "api"
_WRONG_CONNECTOR = "openapi"


def _load_config() -> dict:
    return json.loads(_GENERATED_CONFIG.read_text())


def _widget_ui(config: dict, key: str) -> dict:
    return config["config"]["properties"][key]["ui"]


def test_generated_config_exists() -> None:
    assert _GENERATED_CONFIG.exists(), (
        f"{_GENERATED_CONFIG} missing — run `make generate`."
    )


def test_reuse_selector_uses_api_connector() -> None:
    """Sanity: the REUSE connection selector already filters on the api
    connector via ``ui.connectorName``. Anchors the expected value below."""
    ui = _widget_ui(_load_config(), "connection_qualified_name")
    assert ui.get("connectorName") == _EXPECTED_CONNECTOR


def test_create_widget_uses_api_connector_not_app_id() -> None:
    """CREATE mode must mint connections under ``default/api/``.

    The create-connection widget must surface the ``api`` connector via
    ``ui.connector`` so the frontend does not fall back to the app id
    (``openapi``) when building the new connection's qualifiedName. Without it,
    connections are created as ``default/openapi/{epoch}`` — the regression this
    test guards against.
    """
    ui = _widget_ui(_load_config(), "connection")
    connector = ui.get("connector")
    assert connector != _WRONG_CONNECTOR, (
        "CREATE-connection widget resolves the connector to the app id "
        f"'{_WRONG_CONNECTOR}', so new connections are minted as "
        f"'default/{_WRONG_CONNECTOR}/{{epoch}}'."
    )
    assert connector == _EXPECTED_CONNECTOR, (
        "CREATE-connection widget must surface ui.connector='api' so new "
        f"connections are minted as 'default/{_EXPECTED_CONNECTOR}/{{epoch}}'. "
        f"Got {connector!r}."
    )


def test_both_connection_widgets_agree_on_connector() -> None:
    """Both the CREATE (ConnectionCreator) and REUSE (ConnectionSelector) paths
    must resolve to the same connector type — via their respective ui keys —
    otherwise assets created via one path cannot be reused via the other."""
    config = _load_config()
    create_connector = _widget_ui(config, "connection").get("connector")
    reuse_connector = _widget_ui(config, "connection_qualified_name").get(
        "connectorName"
    )
    assert create_connector == _EXPECTED_CONNECTOR, (
        f"CREATE widget ui.connector is {create_connector!r}, expected "
        f"{_EXPECTED_CONNECTOR!r}."
    )
    assert reuse_connector == _EXPECTED_CONNECTOR, (
        f"REUSE widget ui.connectorName is {reuse_connector!r}, expected "
        f"{_EXPECTED_CONNECTOR!r}."
    )
