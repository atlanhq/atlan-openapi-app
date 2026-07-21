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


def _conditional_required(config: dict, input_key: str, input_value: str) -> list[str]:
    """Return the ``required`` list of the ``anyOf`` branch guarded by
    ``{input_key: input_value}`` (empty if no such branch exists)."""
    for branch in config["config"].get("anyOf", []):
        const = branch.get("properties", {}).get(input_key, {}).get("const")
        if const == input_value:
            return branch.get("required", [])
    return []


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


def test_spec_url_required_only_conditionally() -> None:
    """spec_url must NOT be unconditionally required (that blocked Object
    storage submission), but must still be required via the import_type=URL
    branch. (BLDX-1568)"""
    config = _load_config()
    assert config["config"]["properties"]["spec_url"]["required"] is False, (
        "spec_url.required is True — a field-level `required` makes the "
        "'Specification URL' asterisk unconditional and blocks form submission "
        "in Object storage (CLOUD) mode."
    )
    assert "spec_url" in _conditional_required(config, "import_type", "URL"), (
        "spec_url must be conditionally required in the import_type=URL anyOf "
        "branch (driven by its UIRule)."
    )


def test_cloud_source_required_only_conditionally() -> None:
    """cloud_source must NOT be unconditionally required (that would block
    URL-mode submission), but must still be required via the import_type=CLOUD
    branch."""
    config = _load_config()
    assert config["config"]["properties"]["cloud_source"]["required"] is False, (
        "cloud_source.required is True — a field-level `required` makes the "
        "object-store credential unconditional and blocks form submission in "
        "URL mode."
    )
    assert "cloud_source" in _conditional_required(config, "import_type", "CLOUD"), (
        "cloud_source must be conditionally required in the import_type=CLOUD "
        "anyOf branch (driven by its UIRule)."
    )


def test_connection_widgets_required_only_conditionally() -> None:
    """The CREATE (connection) and REUSE (connection_qualified_name) widgets
    must NOT be unconditionally required — connection_usage toggles which one
    is active, and requiredness is driven by the UIRules. Each must still be
    required in its own connection_usage branch. (BLDX-1568)"""
    config = _load_config()
    props = config["config"]["properties"]

    assert props["connection"]["required"] is False, (
        "connection.required is True — a field-level `required` makes the "
        "CREATE-connection widget unconditional even in REUSE mode."
    )
    assert "connection" in _conditional_required(
        config, "connection_usage", "CREATE"
    ), (
        "connection must be conditionally required in the connection_usage=CREATE branch."
    )

    assert props["connection_qualified_name"]["required"] is False, (
        "connection_qualified_name.required is True — a field-level `required` "
        "makes the REUSE-connection widget unconditional even in CREATE mode."
    )
    assert "connection_qualified_name" in _conditional_required(
        config, "connection_usage", "REUSE"
    ), (
        "connection_qualified_name must be conditionally required in the "
        "connection_usage=REUSE branch."
    )


def test_connection_usage_stays_unconditionally_required() -> None:
    """connection_usage is always shown (it drives the toggle) and so keeps its
    field-level required — guarding against over-eager cleanup. (BLDX-1568)"""
    config = _load_config()
    assert config["config"]["properties"]["connection_usage"]["required"] is True


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
