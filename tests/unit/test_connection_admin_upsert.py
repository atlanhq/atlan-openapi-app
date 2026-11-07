"""Connection upserts must not replay create-time administrator snapshots."""

import json

import pytest
from application_sdk.common.asset_serialization import entity_bytes
from application_sdk.common.entity_envelope import (
    EntityEnvelopePolicy,
    EnvelopeShape,
)
from application_sdk.contracts.types import ConnectionRef

from app.asset_mapper import map_connection


@pytest.mark.parametrize(
    "administrators", [["saved-admin"], []], ids=["stale", "empty"]
)
def test_connection_upsert_omits_saved_administrators(
    administrators: list[str],
) -> None:
    connection = ConnectionRef.model_validate(
        {
            "typeName": "Connection",
            "attributes": {
                "qualifiedName": "default/api/1754574571",
                "name": "api-ontra",
                "adminUsers": administrators,
                "adminGroups": administrators,
                "adminRoles": administrators,
            },
        }
    )
    original = connection.model_dump(by_alias=True)

    entity = json.loads(
        entity_bytes(
            map_connection(connection),
            entity_type="connection",
            envelope=EntityEnvelopePolicy(shape=EnvelopeShape.FLATTENED),
        )
    )

    attributes = entity["attributes"]
    for key in ("adminUsers", "adminGroups", "adminRoles"):
        assert key not in attributes, f"Per-run upsert replays {key}: {attributes[key]}"
    assert entity["typeName"] == "Connection"
    assert attributes["qualifiedName"] == "default/api/1754574571"
    assert attributes["name"] == "api-ontra"
    assert attributes["connectorName"] == "api"
    assert attributes["connectionQualifiedName"] == "default/api/1754574571"
    assert attributes["category"] == "API"
    # Creation still receives the caller's original administrators, not an
    # emptied or mutated snapshot.
    assert connection.model_dump(by_alias=True) == original
