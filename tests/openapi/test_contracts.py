"""Unit tests for OpenAPI connector data contracts.

Tests JSON round-trip serialization for all input/output contracts
using msgspec to verify they survive encode/decode cycles.
"""

import msgspec

from app_framework.app.types import FileReference
from app_framework.credentials import CredentialRef
from pyatlan_v9.model.assets import Connection
from openapi.contracts import (
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
    PublishInput,
    TransformInput,
    TransformOutput,
)


def _round_trip(obj: object, cls: type) -> object:
    """Encode an object to JSON bytes, then decode back to the same type.

    CRITICAL: strict=False is required — Temporal's DataConverter performs
    type coercions that strict=True would reject.
    """
    encoded = msgspec.json.encode(obj)
    return msgspec.json.decode(encoded, type=cls, strict=False)


def _sample_file_ref(path: str = "/tmp/test.jsonl") -> FileReference:
    """Create a sample FileReference for testing."""
    return FileReference(
        local_path=path,
        size_bytes=1024,
        content_type="application/x-ndjson",
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
        assert decoded.openapi_credential is None
        assert decoded.output_dir == ""
        assert decoded.checkpoint_dir == ""
        assert decoded.load_to_atlan is False
        assert decoded.publish_dry_run is False

    def test_round_trip_with_values(self) -> None:
        """All non-default values must survive round-trip."""
        openapi_ref = CredentialRef(name="openapi", credential_type="openapi")
        conn = Connection(
            qualified_name="default/api/test-conn",
            name="test-conn",
            category="API",
            admin_groups=["admins"],
        )
        original = OpenAPIConnectorInput(
            connection=conn,
            import_type="CLOUD",
            spec_url="https://example.com/api.json",
            openapi_credential=openapi_ref,
            output_dir="/tmp/out",
            checkpoint_dir="/tmp/ckpt",
            load_to_atlan=True,
            publish_dry_run=True,
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection is not None
        assert decoded.connection.name == "test-conn"
        assert decoded.connection.qualified_name == "default/api/test-conn"
        assert decoded.import_type == "CLOUD"
        assert decoded.spec_url == "https://example.com/api.json"
        assert decoded.output_dir == "/tmp/out"
        assert decoded.checkpoint_dir == "/tmp/ckpt"
        assert decoded.load_to_atlan is True
        assert decoded.publish_dry_run is True

    def test_round_trip_openapi_credential_ref_fields(self) -> None:
        """CredentialRef fields (name, credential_type, store_name) must survive."""
        ref = CredentialRef(
            name="my-openapi-cred", credential_type="openapi", store_name="vault"
        )
        original = OpenAPIConnectorInput(openapi_credential=ref)
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.openapi_credential is not None
        assert decoded.openapi_credential.name == "my-openapi-cred"
        assert decoded.openapi_credential.credential_type == "openapi"
        assert decoded.openapi_credential.store_name == "vault"

    def test_round_trip_new_fields_defaults(self) -> None:
        """New fields introduced for Pkl contract must have correct defaults."""
        original = OpenAPIConnectorInput()
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection_usage == "REUSE"
        assert decoded.connection_qualified_name == ""
        assert decoded.spec_prefix == ""
        assert decoded.spec_key == ""
        assert decoded.cloud_source == ""
        assert decoded.spec_file is None

    def test_round_trip_connection_usage_reuse(self) -> None:
        """REUSE connection_usage path survives round-trip."""
        original = OpenAPIConnectorInput(
            connection_usage="REUSE",
            connection_qualified_name="default/api/my-connection",
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection_usage == "REUSE"
        assert decoded.connection_qualified_name == "default/api/my-connection"

    def test_round_trip_connection_usage_create(self) -> None:
        """CREATE connection_usage path with Connection object survives round-trip."""
        conn = Connection(
            qualified_name="default/api/new-conn",
            name="new-conn",
            category="API",
        )
        original = OpenAPIConnectorInput(
            connection_usage="CREATE",
            connection=conn,
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.connection_usage == "CREATE"
        assert decoded.connection is not None
        assert decoded.connection.qualified_name == "default/api/new-conn"
        assert decoded.connection_qualified_name == ""

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

    def test_round_trip_spec_file_field(self) -> None:
        """spec_file field (DIRECT import, unsupported) survives round-trip."""
        from app_framework.app.types import FileReference

        ref = FileReference(local_path="/tmp/upload/spec.json")
        original = OpenAPIConnectorInput(
            import_type="DIRECT",
            spec_file=ref,
        )
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.import_type == "DIRECT"
        assert decoded.spec_file is not None
        assert decoded.spec_file.local_path == "/tmp/upload/spec.json"


# =============================================================================
# OpenAPIConnectorOutput
# =============================================================================


class TestOpenAPIConnectorOutput:
    def test_round_trip_defaults(self) -> None:
        """All default values must survive round-trip."""
        original = OpenAPIConnectorOutput()
        decoded = _round_trip(original, OpenAPIConnectorOutput)
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
        assert decoded.output_file.size_bytes == 1024

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
        assert decoded.output_file.size_bytes == 1024


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
        assert decoded.api_spec_file.size_bytes == 1024
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
        """CREATE path: connection object is set; connection_qualified_name is derived from it."""
        spec_ref = _sample_file_ref("/tmp/diff/changed_api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/diff/changed_api_path.jsonl")
        conn = Connection(
            qualified_name="default/api/conn",
            name="conn",
            category="API",
            admin_groups=["admins"],
        )
        original = TransformInput(
            api_spec_file=spec_ref,
            api_path_file=path_ref,
            connection=conn,
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
        assert decoded.connection.qualified_name == "default/api/conn"
        assert decoded.connection_qualified_name == "default/api/conn"
        assert decoded.output_dir == "/tmp/out"
        assert decoded.workflow_id == "wf-abc123"
        assert decoded.workflow_type == "openapi"
        assert decoded.workflow_run_at_ms == 1700000000000

    def test_input_round_trip_reuse_path(self) -> None:
        """REUSE path: connection is None; connection_qualified_name carries the QN."""
        spec_ref = _sample_file_ref("/tmp/diff/changed_api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/diff/changed_api_path.jsonl")
        original = TransformInput(
            api_spec_file=spec_ref,
            api_path_file=path_ref,
            connection=None,
            connection_qualified_name="default/api/existing-conn",
            output_dir="/tmp/out",
            workflow_id="wf-abc123",
            workflow_type="openapi",
            workflow_run_at_ms=1700000000000,
        )
        decoded = _round_trip(original, TransformInput)
        assert decoded.connection is None
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
        assert decoded.output_file.size_bytes == 1024
        assert decoded.api_spec_count == 1
        assert decoded.api_path_count == 17

    def test_output_file_none_when_no_changes(self) -> None:
        original = TransformOutput(api_spec_count=0, api_path_count=0)
        decoded = _round_trip(original, TransformOutput)
        assert decoded.output_file is None


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
