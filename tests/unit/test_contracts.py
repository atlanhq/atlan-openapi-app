"""Unit tests for OpenAPI connector data contracts.

Tests JSON round-trip serialization for all input/output contracts
using Pydantic's own model_dump_json / model_validate_json.
"""

from pydantic import BaseModel

from application_sdk.contracts.types import ConnectionRef, FileReference
from application_sdk.credentials.ref import CredentialRef
from app.contracts import (
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
    PublishInput,
    TransformInput,
    TransformOutput,
)


def _round_trip(obj: BaseModel, cls: type[BaseModel]) -> BaseModel:
    """Encode a Pydantic model to JSON, then decode back to the same type."""
    return cls.model_validate_json(obj.model_dump_json())


def _sample_file_ref(path: str = "/tmp/test.jsonl") -> FileReference:
    """Create a sample FileReference for testing."""
    return FileReference(local_path=path)


def _make_connection_ref(qn: str, name: str = "") -> ConnectionRef:
    return ConnectionRef.model_validate(
        {"typeName": "Connection", "attributes": {"qualifiedName": qn, "name": name}}
    )


# =============================================================================
# OpenAPIConnectorInput
# =============================================================================


class TestOpenAPIConnectorInput:
    def test_round_trip_defaults(self) -> None:
        """All default values must survive round-trip."""
        original = OpenAPIConnectorInput()
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection is None
        assert decoded.import_type == "URL"
        assert decoded.spec_url == ""
        assert decoded.output_dir == ""
        assert decoded.checkpoint_dir == ""
        assert decoded.load_to_atlan is True
        assert decoded.publish_dry_run is False

    def test_round_trip_with_values(self) -> None:
        """All non-default values must survive round-trip."""
        original = OpenAPIConnectorInput(
            connection=_make_connection_ref("default/api/test-conn", "test-conn"),
            import_type="CLOUD",
            spec_url="https://example.com/api.json",
            output_dir="/tmp/out",
            checkpoint_dir="/tmp/ckpt",
            load_to_atlan=False,
            publish_dry_run=True,
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection is not None
        assert decoded.connection.attributes.name == "test-conn"
        assert decoded.connection.attributes.qualified_name == "default/api/test-conn"
        assert decoded.import_type == "CLOUD"
        assert decoded.spec_url == "https://example.com/api.json"
        assert decoded.output_dir == "/tmp/out"
        assert decoded.checkpoint_dir == "/tmp/ckpt"
        assert decoded.load_to_atlan is False
        assert decoded.publish_dry_run is True

    def test_round_trip_cloud_input_defaults(self) -> None:
        """Cloud import fields have correct defaults."""
        original = OpenAPIConnectorInput()
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.spec_prefix == ""
        assert decoded.spec_key == ""
        assert decoded.cloud_source == ""

    def test_round_trip_cloud_import_fields(self) -> None:
        """Cloud import fields survive round-trip."""
        original = OpenAPIConnectorInput(
            import_type="CLOUD",
            spec_prefix="path/to/specs",
            spec_key="openapi.json",
            cloud_source="cred-guid-abc123",
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.import_type == "CLOUD"
        assert decoded.spec_prefix == "path/to/specs"
        assert decoded.spec_key == "openapi.json"
        assert decoded.cloud_source == "cred-guid-abc123"


# =============================================================================
# OpenAPIConnectorOutput
# =============================================================================


class TestOpenAPIConnectorOutput:
    def test_round_trip_defaults(self) -> None:
        """All default values must survive round-trip."""
        original = OpenAPIConnectorOutput()
        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.connection_qualified_name == ""
        assert decoded.transformed_data_prefix == ""
        assert decoded.api_spec_count == 0
        assert decoded.api_path_count == 0
        assert decoded.output_file is None
        assert decoded.total_scanned == 0
        assert decoded.atlan_loaded_count == 0
        assert decoded.atlan_created_count == 0
        assert decoded.atlan_updated_count == 0
        assert decoded.atlan_validated_count == 0
        assert decoded.atlan_error_count == 0
        assert decoded.atlan_errors == []
        assert decoded.publish_completed is False

    def test_round_trip_with_values(self) -> None:
        """Non-default values survive round-trip."""
        original = OpenAPIConnectorOutput(
            api_spec_count=1,
            api_path_count=42,
            output_file=_sample_file_ref("/tmp/out/openapi_metadata.jsonl"),
            total_scanned=43,
            atlan_loaded_count=43,
            atlan_created_count=40,
            atlan_updated_count=3,
            atlan_validated_count=0,
            atlan_error_count=0,
        )
        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.api_spec_count == 1
        assert decoded.api_path_count == 42
        assert decoded.total_scanned == 43
        assert decoded.atlan_loaded_count == 43
        assert decoded.atlan_created_count == 40
        assert decoded.atlan_updated_count == 3
        assert decoded.output_file is not None
        assert decoded.output_file.local_path == "/tmp/out/openapi_metadata.jsonl"
        assert decoded.output_file.local_path is not None

    def test_output_file_none_by_default(self) -> None:
        """output_file is None when no records were extracted."""
        original = OpenAPIConnectorOutput()
        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.output_file is None

    def test_file_reference_local_path_survives(self) -> None:
        """FileReference.local_path survives round-trip."""
        ref = _sample_file_ref("/custom/path/output.jsonl")
        original = OpenAPIConnectorOutput(output_file=ref)
        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.output_file is not None
        assert decoded.output_file.local_path == "/custom/path/output.jsonl"
        assert decoded.output_file.local_path is not None


# =============================================================================
# ExtractSpecInput / ExtractSpecOutput
# =============================================================================


class TestExtractSpecContracts:
    def test_input_round_trip_defaults(self) -> None:
        original = ExtractSpecInput()
        decoded = _round_trip(original, ExtractSpecInput)
        assert decoded.spec_url == ""
        assert decoded.connection_qualified_name == ""
        assert decoded.output_dir == ""
        assert decoded.openapi_credential is None

    def test_input_round_trip_with_values(self) -> None:
        ref = CredentialRef(name="openapi", credential_type="openapi")
        original = ExtractSpecInput(
            spec_url="https://petstore3.swagger.io/api/v3/openapi.json",
            connection_qualified_name="default/api/test-conn",
            output_dir="/tmp/raw",
            openapi_credential=ref,
        )
        decoded = _round_trip(original, ExtractSpecInput)
        assert decoded.spec_url == "https://petstore3.swagger.io/api/v3/openapi.json"
        assert decoded.connection_qualified_name == "default/api/test-conn"
        assert decoded.output_dir == "/tmp/raw"
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.name == "openapi"
        assert decoded.openapi_credential.credential_type == "openapi"

    def test_input_credential_ref_fields_survive(self) -> None:
        ref = CredentialRef(name="cred", credential_type="openapi", store_name="vault")
        original = ExtractSpecInput(openapi_credential=ref)
        decoded = _round_trip(original, ExtractSpecInput)
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.store_name == "vault"

    def test_output_round_trip_defaults(self) -> None:
        original = ExtractSpecOutput()
        decoded = _round_trip(original, ExtractSpecOutput)
        assert decoded.api_spec_file is None
        assert decoded.api_path_file is None
        assert decoded.api_spec_count == 0
        assert decoded.api_path_count == 0

    def test_output_round_trip_with_values(self) -> None:
        spec_ref = _sample_file_ref("/tmp/raw/api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/raw/api_path.jsonl")
        original = ExtractSpecOutput(
            api_spec_file=spec_ref,
            api_path_file=path_ref,
            api_spec_count=1,
            api_path_count=17,
        )
        decoded = _round_trip(original, ExtractSpecOutput)
        assert decoded.api_spec_file is not None
        assert decoded.api_spec_file.local_path == "/tmp/raw/api_spec.jsonl"
        assert decoded.api_spec_file.local_path is not None
        assert decoded.api_path_file is not None
        assert decoded.api_path_file.local_path == "/tmp/raw/api_path.jsonl"
        assert decoded.api_spec_count == 1
        assert decoded.api_path_count == 17


# =============================================================================
# TransformInput / TransformOutput
# =============================================================================


class TestTransformContracts:
    def test_input_round_trip_defaults(self) -> None:
        original = TransformInput()
        decoded = _round_trip(original, TransformInput)
        assert decoded.api_spec_file is None
        assert decoded.api_path_file is None
        assert decoded.connection is None
        assert decoded.connection_qualified_name == ""
        assert decoded.output_dir == ""
        assert decoded.workflow_id == ""
        assert decoded.workflow_type == ""
        assert decoded.workflow_run_at_ms == 0

    def test_input_round_trip_create_path(self) -> None:
        """connection object is set; connection_qualified_name is derived from it."""
        spec_ref = _sample_file_ref("/tmp/diff/changed_api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/diff/changed_api_path.jsonl")
        original = TransformInput(
            api_spec_file=spec_ref,
            api_path_file=path_ref,
            connection=_make_connection_ref("default/api/conn", "conn"),
            connection_qualified_name="default/api/conn",
            output_dir="/tmp/out",
            workflow_id="wf-abc123",
            workflow_type="openapi",
            workflow_run_at_ms=1700000000000,
        )
        decoded = _round_trip(original, TransformInput)
        assert decoded.api_spec_file is not None
        assert decoded.api_spec_file.local_path == "/tmp/diff/changed_api_spec.jsonl"
        assert decoded.api_path_file is not None
        assert decoded.connection is not None
        assert decoded.connection.attributes.qualified_name == "default/api/conn"
        assert decoded.connection_qualified_name == "default/api/conn"
        assert decoded.output_dir == "/tmp/out"
        assert decoded.workflow_id == "wf-abc123"
        assert decoded.workflow_type == "openapi"
        assert decoded.workflow_run_at_ms == 1700000000000

    def test_input_round_trip_with_connection(self) -> None:
        """connection is always required; connection_qualified_name is derived from it."""
        spec_ref = _sample_file_ref("/tmp/diff/changed_api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/diff/changed_api_path.jsonl")
        original = TransformInput(
            api_spec_file=spec_ref,
            api_path_file=path_ref,
            connection=_make_connection_ref(
                "default/api/existing-conn", "existing-conn"
            ),
            connection_qualified_name="default/api/existing-conn",
            output_dir="/tmp/out",
            workflow_id="wf-abc123",
            workflow_type="openapi",
            workflow_run_at_ms=1700000000000,
        )
        decoded = _round_trip(original, TransformInput)
        assert decoded.connection is not None
        assert (
            decoded.connection.attributes.qualified_name == "default/api/existing-conn"
        )
        assert decoded.connection_qualified_name == "default/api/existing-conn"
        assert decoded.api_spec_file is not None
        assert decoded.api_path_file is not None
        assert decoded.output_dir == "/tmp/out"
        assert decoded.workflow_id == "wf-abc123"
        assert decoded.workflow_type == "openapi"
        assert decoded.workflow_run_at_ms == 1700000000000

    def test_output_round_trip_defaults(self) -> None:
        original = TransformOutput()
        decoded = _round_trip(original, TransformOutput)
        assert decoded.output_file is None
        assert decoded.api_spec_count == 0
        assert decoded.api_path_count == 0

    def test_output_round_trip_with_values(self) -> None:
        out_ref = _sample_file_ref("/tmp/out/openapi_metadata.jsonl")
        original = TransformOutput(
            output_file=out_ref,
            api_spec_count=1,
            api_path_count=17,
        )
        decoded = _round_trip(original, TransformOutput)
        assert decoded.output_file is not None
        assert decoded.output_file.local_path == "/tmp/out/openapi_metadata.jsonl"
        assert decoded.output_file.local_path is not None
        assert decoded.api_spec_count == 1
        assert decoded.api_path_count == 17

    def test_output_file_none_when_no_changes(self) -> None:
        original = TransformOutput(api_spec_count=0, api_path_count=0)
        decoded = _round_trip(original, TransformOutput)
        assert decoded.output_file is None


# =============================================================================
# ConnectionRef serialization
# =============================================================================


class TestConnectionRefSerialization:
    """Pin the camelCase wire-format requirement for ConnectionRef.

    ConnectionRef.model_dump(by_alias=True) must produce camelCase attribute
    keys. Without by_alias=True the nested ConnectionAttributes model serializes
    in snake_case because Pydantic does not inherit serialize_by_alias from the
    parent model — which is the bug fixed in connector.py.
    """

    def test_model_dump_by_alias_produces_camel_case(self) -> None:
        """model_dump(by_alias=True) must produce camelCase attribute keys."""
        ref = ConnectionRef.model_validate(
            {
                "typeName": "Connection",
                "attributes": {
                    "qualifiedName": "default/api/test",
                    "name": "test",
                    "connectorName": "api",
                    "adminGroups": ["admins"],
                    "adminUsers": ["user1"],
                    "adminRoles": [],
                },
            }
        )
        d = ref.model_dump(by_alias=True)
        attrs = d["attributes"]
        assert "qualifiedName" in attrs, "qualifiedName must be camelCase"
        assert "connectorName" in attrs, "connectorName must be camelCase"
        assert "adminGroups" in attrs, "adminGroups must be camelCase"
        assert "adminUsers" in attrs, "adminUsers must be camelCase"
        assert "adminRoles" in attrs, "adminRoles must be camelCase"
        # Confirm snake_case is absent
        assert "qualified_name" not in attrs
        assert "connector_name" not in attrs
        assert "admin_groups" not in attrs

    def test_model_dump_without_by_alias_does_not_produce_camel_case(self) -> None:
        """Regression guard: model_dump() without by_alias produces snake_case attributes.

        This test documents the Pydantic behaviour that motivated the fix: the
        outer ConnectionRef has serialize_by_alias=True, but that flag is NOT
        inherited by the nested ConnectionAttributes model, so plain model_dump()
        leaves attribute keys in snake_case.
        """
        ref = _make_connection_ref("default/api/test", "test")
        d = ref.model_dump()
        attrs = d["attributes"]
        assert "qualified_name" in attrs, (
            "plain model_dump() still yields snake_case — "
            "connector must always use model_dump(by_alias=True)"
        )

    def test_top_level_key_is_camel_case(self) -> None:
        """typeName (not type_name) must appear at the top level."""
        ref = _make_connection_ref("default/api/test", "test")
        d = ref.model_dump(by_alias=True)
        assert "typeName" in d
        assert "type_name" not in d

    def test_snake_case_input_also_serializes_to_camel_case(self) -> None:
        """ConnectionRef built with snake_case field names must still serialize to camelCase."""
        ref = ConnectionRef.model_validate(
            {
                "typeName": "Connection",
                "attributes": {
                    "qualified_name": "default/api/test",
                    "name": "test",
                    "connector_name": "api",
                    "admin_groups": ["admins"],
                },
            }
        )
        d = ref.model_dump(by_alias=True)
        attrs = d["attributes"]
        assert "qualifiedName" in attrs
        assert "connectorName" in attrs
        assert "adminGroups" in attrs
        assert "qualified_name" not in attrs


# =============================================================================
# PublishInput
# =============================================================================


class TestPublishInput:
    def test_round_trip_defaults(self) -> None:
        original = PublishInput()
        decoded = _round_trip(original, PublishInput)
        assert decoded.connection_qualified_name == ""
        assert decoded.transformed_data_prefix == ""
        assert decoded.publish_state_prefix == ""
        assert decoded.current_state_prefix == ""
        assert decoded.connection_creation_enabled is True
        assert decoded.executor_enabled is True
        assert decoded.connection_entity == {}

    def test_round_trip_with_values(self) -> None:
        original = PublishInput(
            connection_qualified_name="default/api/my-conn",
            transformed_data_prefix="argo-artifacts/default/api/my-conn/transformed-metadata/run-1",
            publish_state_prefix="persistent-artifacts/apps/atlan-publish-app/state/default/api/my-conn/publish-state",
            current_state_prefix="argo-artifacts/default/api/my-conn/current-state",
            connection_creation_enabled=False,
            executor_enabled=False,
            connection_entity={
                "typeName": "Connection",
                "qualifiedName": "default/api/my-conn",
            },
        )
        decoded = _round_trip(original, PublishInput)
        assert decoded.connection_qualified_name == "default/api/my-conn"
        assert "transformed-metadata" in decoded.transformed_data_prefix
        assert decoded.connection_creation_enabled is False
        assert decoded.executor_enabled is False
        assert decoded.connection_entity.get("typeName") == "Connection"

    def test_connection_entity_built_from_connection_ref_is_camel_case(self) -> None:
        """connection_entity must use camelCase keys as publish-app expects.

        Mirrors exactly how connector.py builds connection_entity:
            connection_dict = connection.model_dump(by_alias=True)
            PublishInput(..., connection_entity=connection_dict)
        """
        connection = ConnectionRef.model_validate(
            {
                "typeName": "Connection",
                "attributes": {
                    "qualifiedName": "default/api/my-conn",
                    "name": "my-conn",
                    "connectorName": "api",
                    "category": "API",
                    "adminGroups": ["admins"],
                    "adminUsers": [],
                    "adminRoles": [],
                },
            }
        )
        connection_dict = connection.model_dump(by_alias=True)
        publish_input = PublishInput(
            connection_qualified_name="default/api/my-conn",
            connection_entity=connection_dict,
        )
        attrs = publish_input.connection_entity["attributes"]
        assert "qualifiedName" in attrs, "publish-app requires camelCase qualifiedName"
        assert "connectorName" in attrs, "publish-app requires camelCase connectorName"
        assert "adminGroups" in attrs, "publish-app requires camelCase adminGroups"
        assert "qualified_name" not in attrs
        assert "connector_name" not in attrs
        assert "admin_groups" not in attrs
