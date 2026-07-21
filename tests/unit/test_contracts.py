"""Unit tests for OpenAPI connector data contracts.

Tests JSON round-trip serialization for all input/output contracts
using Pydantic's own model_dump_json / model_validate_json.
"""

from pydantic import BaseModel

from application_sdk.contracts.types import ConnectionRef, FileReference
from application_sdk.credentials.ref import CredentialRef
from app.contracts import (
    DownloadCloudSpecInput,
    DownloadCloudSpecOutput,
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
    PublishInput,
    ResolveSourceTypeInput,
    ResolveSourceTypeOutput,
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
        assert decoded.connection.attributes.qualified_name == ""
        # The spec source now lives entirely in a single source credential;
        # import_type / spec_url / spec_prefix / spec_key / cloud_source are gone.
        assert decoded.openapi_credential is None
        assert decoded.output_dir == ""
        assert decoded.checkpoint_dir == ""
        assert decoded.load_to_atlan is True
        assert decoded.publish_dry_run is False
        # CONNECT-55: default is REUSE, matching the CSA reference package.
        assert decoded.connection_usage == "REUSE"
        assert decoded.connection_qualified_name == ""

    def test_connection_usage_and_qn_round_trip(self) -> None:
        """connection_usage + connection_qualified_name survive round-trip — the
        REUSE path selects an existing connection via connection_qualified_name
        (CONNECT-55)."""
        original = OpenAPIConnectorInput(
            connection_usage="CREATE",
            connection_qualified_name="default/api/existing",
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection_usage == "CREATE"
        assert decoded.connection_qualified_name == "default/api/existing"

    def test_round_trip_with_values(self) -> None:
        """All non-default values must survive round-trip."""
        ref = CredentialRef(name="openapi_source", credential_type="openapi")
        original = OpenAPIConnectorInput(
            connection=_make_connection_ref("default/api/test-conn", "test-conn"),
            openapi_credential=ref,
            output_dir="/tmp/out",
            checkpoint_dir="/tmp/ckpt",
            load_to_atlan=False,
            publish_dry_run=True,
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection is not None
        assert decoded.connection.attributes.name == "test-conn"
        assert decoded.connection.attributes.qualified_name == "default/api/test-conn"
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.name == "openapi_source"
        assert decoded.output_dir == "/tmp/out"
        assert decoded.checkpoint_dir == "/tmp/ckpt"
        assert decoded.load_to_atlan is False
        assert decoded.publish_dry_run is True

    def test_openapi_credential_round_trip(self) -> None:
        """The single source credential survives round-trip — it is the only
        spec-source field on the input (the source now lives entirely in the
        resolved credential's authType)."""
        ref = CredentialRef(
            name="openapi_source", credential_type="openapi", store_name="vault"
        )
        original = OpenAPIConnectorInput(openapi_credential=ref)
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.name == "openapi_source"
        assert decoded.openapi_credential.store_name == "vault"

    def test_openapi_credential_guid_round_trip(self) -> None:
        """A GUID-shaped source credential ref survives round-trip (the
        object-store / platform-issued path)."""
        ref = CredentialRef(credential_guid="cred-guid-abc123")
        original = OpenAPIConnectorInput(openapi_credential=ref)
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.credential_guid == "cred-guid-abc123"


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
        assert decoded.publish_completed is False
        # CONNECT-55: default is False — the publish node reads this via
        # `$.extract.outputs.assertion_only_enabled`, so it must always be
        # present and default to the normal (full-diff) publish path.
        assert decoded.assertion_only_enabled is False

    def test_assertion_only_enabled_round_trips(self) -> None:
        """assertion_only_enabled=True survives round-trip and is present in the
        serialized payload — the publish node's JSONPath resolution fails
        outright if the key is absent (CONNECT-55)."""
        original = OpenAPIConnectorOutput(assertion_only_enabled=True)
        assert "assertion_only_enabled" in original.model_dump()
        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.assertion_only_enabled is True

    def test_round_trip_with_values(self) -> None:
        """Non-default values survive round-trip."""
        original = OpenAPIConnectorOutput(
            api_spec_count=1,
            api_path_count=42,
            output_file=_sample_file_ref("/tmp/out/openapi_metadata.jsonl"),
            total_scanned=43,
        )
        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.api_spec_count == 1
        assert decoded.api_path_count == 42
        assert decoded.total_scanned == 43
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

    def test_publish_paths_auto_derive_from_connection_qn(self) -> None:
        """publish_state_prefix/staging_data_prefix/current_state_prefix must be
        auto-derived (via PublishInputMixin) and non-empty whenever
        connection_qualified_name is set — the publish DAG node reads them via
        `$.extract.outputs.*` JSONPath, which fails outright (not just with an
        empty value) if the key is absent (DISTR-752-style regression)."""
        original = OpenAPIConnectorOutput(
            connection_qualified_name="default/api/1234567890"
        )
        assert original.publish_state_prefix == (
            "persistent-artifacts/apps/atlan-publish-app/state/default/api/1234567890/publish-state"
        )
        assert original.staging_data_prefix == (
            "persistent-artifacts/apps/atlan-publish-app/state/default/api/1234567890"
        )
        assert original.current_state_prefix == (
            "argo-artifacts/default/api/1234567890/current-state"
        )

        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.publish_state_prefix == original.publish_state_prefix
        assert decoded.staging_data_prefix == original.staging_data_prefix
        assert decoded.current_state_prefix == original.current_state_prefix

    def test_publish_paths_serialized_even_when_empty(self) -> None:
        """The publish-path keys must always be present in serialized output,
        even with no connection_qualified_name — a missing key (not merely an
        empty value) is what breaks the publish node's JSONPath resolution."""
        original = OpenAPIConnectorOutput()
        dumped = original.model_dump()
        assert "publish_state_prefix" in dumped
        assert "staging_data_prefix" in dumped
        assert "current_state_prefix" in dumped


# =============================================================================
# ExtractSpecInput / ExtractSpecOutput
# =============================================================================


class TestExtractSpecContracts:
    def test_input_round_trip_defaults(self) -> None:
        original = ExtractSpecInput()
        decoded = _round_trip(original, ExtractSpecInput)
        assert decoded.spec_url == ""
        assert decoded.connection_qualified_name == ""
        assert decoded.openapi_credential is None

    def test_input_round_trip_with_values(self) -> None:
        ref = CredentialRef(name="openapi", credential_type="openapi")
        original = ExtractSpecInput(
            spec_url="https://petstore3.swagger.io/api/v3/openapi.json",
            connection_qualified_name="default/api/test-conn",
            openapi_credential=ref,
        )
        decoded = _round_trip(original, ExtractSpecInput)
        assert decoded.spec_url == "https://petstore3.swagger.io/api/v3/openapi.json"
        assert decoded.connection_qualified_name == "default/api/test-conn"
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
# DownloadCloudSpecInput / DownloadCloudSpecOutput
# =============================================================================


class TestDownloadCloudSpecContracts:
    def test_input_round_trip_defaults(self) -> None:
        original = DownloadCloudSpecInput()
        decoded = _round_trip(original, DownloadCloudSpecInput)
        assert decoded.openapi_credential is None

    def test_input_credential_round_trip(self) -> None:
        """The object-store location (auth + spec_prefix/spec_key) now lives in
        the resolved credential; the task carries only the credential ref."""
        ref = CredentialRef(credential_guid="cloud-guid-1")
        original = DownloadCloudSpecInput(openapi_credential=ref)
        decoded = _round_trip(original, DownloadCloudSpecInput)
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.credential_guid == "cloud-guid-1"

    def test_output_round_trip_defaults(self) -> None:
        original = DownloadCloudSpecOutput()
        decoded = _round_trip(original, DownloadCloudSpecOutput)
        assert decoded.spec_files == []

    def test_output_round_trip_with_values(self) -> None:
        original = DownloadCloudSpecOutput(
            spec_files=[_sample_file_ref("/tmp/cloud/spec.json")]
        )
        decoded = _round_trip(original, DownloadCloudSpecOutput)
        assert len(decoded.spec_files) == 1
        assert decoded.spec_files[0].local_path == "/tmp/cloud/spec.json"


# =============================================================================
# ResolveSourceTypeInput / ResolveSourceTypeOutput
# =============================================================================


class TestResolveSourceTypeContracts:
    def test_input_round_trip_defaults(self) -> None:
        original = ResolveSourceTypeInput()
        decoded = _round_trip(original, ResolveSourceTypeInput)
        assert decoded.openapi_credential is None

    def test_input_credential_round_trip(self) -> None:
        ref = CredentialRef(credential_guid="src-guid-1")
        original = ResolveSourceTypeInput(openapi_credential=ref)
        decoded = _round_trip(original, ResolveSourceTypeInput)
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.credential_guid == "src-guid-1"

    def test_output_round_trip_defaults(self) -> None:
        original = ResolveSourceTypeOutput()
        decoded = _round_trip(original, ResolveSourceTypeOutput)
        assert decoded.auth_type == ""

    def test_output_round_trip_with_values(self) -> None:
        original = ResolveSourceTypeOutput(auth_type="s3")
        decoded = _round_trip(original, ResolveSourceTypeOutput)
        assert decoded.auth_type == "s3"


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
        assert decoded.emit_connection is True
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
        assert decoded.connection_entity is None

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
        assert decoded.connection_entity is not None
        assert decoded.connection_entity.type_name == "Connection"

    def test_connection_entity_serializes_camel_case_for_publish_app(self) -> None:
        """connection_entity must serialize to camelCase keys as publish-app expects."""
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
        publish_input = PublishInput(
            connection_qualified_name="default/api/my-conn",
            connection_entity=connection,
        )
        assert publish_input.connection_entity is not None
        attrs = publish_input.connection_entity.model_dump(by_alias=True)["attributes"]
        assert "qualifiedName" in attrs, "publish-app requires camelCase qualifiedName"
        assert "connectorName" in attrs, "publish-app requires camelCase connectorName"
        assert "adminGroups" in attrs, "publish-app requires camelCase adminGroups"
        assert "qualified_name" not in attrs
        assert "connector_name" not in attrs
        assert "admin_groups" not in attrs
