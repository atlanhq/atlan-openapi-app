"""Unit tests for OpenAPI connector data contracts.

Tests JSON round-trip serialization for all input/output contracts
using msgspec to verify they survive encode/decode cycles.
"""

import msgspec

from app_framework.app.types import FileReference
from app_framework.credentials import CredentialRef
from pyatlan.models.connection import Connection
from openapi.contracts import (
    DiffInput,
    DiffOutput,
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
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
        assert decoded.atlan_credential is None
        assert decoded.loader_batch_size == 20
        assert decoded.loader_chunk_size == 10000
        assert decoded.loader_max_chunks_per_execution == 500
        assert decoded.loader_save_timeout is None
        assert decoded.loader_dry_run is False

    def test_round_trip_with_values(self) -> None:
        """All non-default values must survive round-trip."""
        atlan_ref = CredentialRef(name="atlan", credential_type="atlan_api_token")
        openapi_ref = CredentialRef(name="openapi", credential_type="openapi")
        conn = Connection(qualified_name="default/api/test-conn", name="test-conn")
        original = OpenAPIConnectorInput(
            connection=conn,
            import_type="CLOUD",
            spec_url="https://example.com/api.json",
            openapi_credential=openapi_ref,
            output_dir="/tmp/out",
            checkpoint_dir="/tmp/ckpt",
            load_to_atlan=True,
            atlan_credential=atlan_ref,
            loader_batch_size=50,
            loader_chunk_size=5000,
            loader_max_chunks_per_execution=100,
            loader_save_timeout=30.0,
            loader_dry_run=True,
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
        assert decoded.loader_batch_size == 50
        assert decoded.loader_chunk_size == 5000
        assert decoded.loader_max_chunks_per_execution == 100
        assert decoded.loader_save_timeout == 30.0
        assert decoded.loader_dry_run is True

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

    def test_round_trip_atlan_credential_ref_fields(self) -> None:
        """Atlan CredentialRef fields must survive round-trip."""
        ref = CredentialRef(
            name="atlan-cred", credential_type="atlan_api_token", store_name="default"
        )
        original = OpenAPIConnectorInput(atlan_credential=ref)
        decoded = _round_trip(original, OpenAPIConnectorInput)
        assert decoded.atlan_credential is not None
        assert decoded.atlan_credential.name == "atlan-cred"
        assert decoded.atlan_credential.credential_type == "atlan_api_token"
        assert decoded.atlan_credential.store_name == "default"


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
        assert decoded.new_count == 0
        assert decoded.changed_count == 0
        assert decoded.unchanged_count == 0
        assert decoded.deleted_count == 0
        assert decoded.checkpoint_ref is None
        assert decoded.atlan_loaded_count == 0
        assert decoded.atlan_created_count == 0
        assert decoded.atlan_updated_count == 0
        assert decoded.atlan_validated_count == 0
        assert decoded.atlan_error_count == 0
        assert decoded.atlan_errors == []

    def test_round_trip_with_values(self) -> None:
        """Non-default values survive round-trip."""
        original = OpenAPIConnectorOutput(
            api_spec_count=1,
            api_path_count=42,
            output_file=_sample_file_ref("/tmp/out/openapi_metadata.jsonl"),
            total_scanned=44,
            new_count=40,
            changed_count=3,
            unchanged_count=1,
            deleted_count=0,
            checkpoint_ref=_sample_file_ref("/tmp/ckpt/checkpoint.db"),
            atlan_loaded_count=43,
            atlan_created_count=40,
            atlan_updated_count=3,
            atlan_validated_count=0,
            atlan_error_count=0,
        )
        decoded = _round_trip(original, OpenAPIConnectorOutput)
        assert decoded.api_spec_count == 1
        assert decoded.api_path_count == 42
        assert decoded.total_scanned == 44
        assert decoded.new_count == 40
        assert decoded.changed_count == 3
        assert decoded.unchanged_count == 1
        assert decoded.deleted_count == 0
        assert decoded.atlan_loaded_count == 43
        assert decoded.atlan_created_count == 40
        assert decoded.atlan_updated_count == 3
        assert decoded.output_file is not None
        assert decoded.output_file.local_path == "/tmp/out/openapi_metadata.jsonl"
        assert decoded.output_file.size_bytes == 1024
        assert decoded.checkpoint_ref is not None
        assert decoded.checkpoint_ref.local_path == "/tmp/ckpt/checkpoint.db"

    def test_output_file_none_by_default(self) -> None:
        """output_file is None when no changes were found."""
        original = OpenAPIConnectorOutput(unchanged_count=5)
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
# DiffInput / DiffOutput
# =============================================================================


class TestDiffContracts:
    def test_input_round_trip_defaults(self) -> None:
        original = DiffInput()
        decoded = _round_trip(original, DiffInput)
        assert decoded.api_spec_file is None
        assert decoded.api_path_file is None
        assert decoded.connection is None
        assert decoded.checkpoint_dir == ""
        assert decoded.output_dir == ""

    def test_input_round_trip_with_values(self) -> None:
        spec_ref = _sample_file_ref("/tmp/raw/api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/raw/api_path.jsonl")
        conn = Connection(qualified_name="default/api/conn", name="conn")
        original = DiffInput(
            api_spec_file=spec_ref,
            api_path_file=path_ref,
            connection=conn,
            checkpoint_dir="/tmp/ckpt",
            output_dir="/tmp/diff",
        )
        decoded = _round_trip(original, DiffInput)
        assert decoded.api_spec_file is not None
        assert decoded.api_spec_file.local_path == "/tmp/raw/api_spec.jsonl"
        assert decoded.api_path_file is not None
        assert decoded.connection is not None
        assert decoded.connection.qualified_name == "default/api/conn"
        assert decoded.checkpoint_dir == "/tmp/ckpt"
        assert decoded.output_dir == "/tmp/diff"

    def test_input_file_ref_size_bytes_survives(self) -> None:
        ref = FileReference(local_path="/tmp/test.jsonl", size_bytes=2048)
        original = DiffInput(api_spec_file=ref)
        decoded = _round_trip(original, DiffInput)
        assert decoded.api_spec_file is not None
        assert decoded.api_spec_file.size_bytes == 2048

    def test_output_round_trip_defaults(self) -> None:
        original = DiffOutput()
        decoded = _round_trip(original, DiffOutput)
        assert decoded.changed_api_spec_file is None
        assert decoded.changed_api_path_file is None
        assert decoded.new_count == 0
        assert decoded.changed_count == 0
        assert decoded.unchanged_count == 0
        assert decoded.deleted_count == 0
        assert decoded.total_scanned == 0
        assert decoded.checkpoint_new_path == ""

    def test_output_round_trip_with_values(self) -> None:
        spec_ref = _sample_file_ref("/tmp/diff/changed_api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/diff/changed_api_path.jsonl")
        original = DiffOutput(
            changed_api_spec_file=spec_ref,
            changed_api_path_file=path_ref,
            new_count=5,
            changed_count=2,
            unchanged_count=10,
            deleted_count=1,
            total_scanned=18,
            checkpoint_new_path="/tmp/ckpt/.new",
        )
        decoded = _round_trip(original, DiffOutput)
        assert decoded.changed_api_spec_file is not None
        assert (
            decoded.changed_api_spec_file.local_path
            == "/tmp/diff/changed_api_spec.jsonl"
        )
        assert decoded.changed_api_path_file is not None
        assert decoded.new_count == 5
        assert decoded.changed_count == 2
        assert decoded.unchanged_count == 10
        assert decoded.deleted_count == 1
        assert decoded.total_scanned == 18
        assert decoded.checkpoint_new_path == "/tmp/ckpt/.new"


# =============================================================================
# TransformInput / TransformOutput
# =============================================================================


class TestTransformContracts:
    def test_input_round_trip_defaults(self) -> None:
        original = TransformInput()
        decoded = _round_trip(original, TransformInput)
        assert decoded.changed_api_spec_file is None
        assert decoded.changed_api_path_file is None
        assert decoded.connection is None
        assert decoded.output_dir == ""
        assert decoded.workflow_id == ""
        assert decoded.workflow_type == ""
        assert decoded.workflow_run_at_ms == 0

    def test_input_round_trip_with_values(self) -> None:
        spec_ref = _sample_file_ref("/tmp/diff/changed_api_spec.jsonl")
        path_ref = _sample_file_ref("/tmp/diff/changed_api_path.jsonl")
        conn = Connection(qualified_name="default/api/conn", name="conn")
        original = TransformInput(
            changed_api_spec_file=spec_ref,
            changed_api_path_file=path_ref,
            connection=conn,
            output_dir="/tmp/out",
            workflow_id="wf-abc123",
            workflow_type="openapi",
            workflow_run_at_ms=1700000000000,
        )
        decoded = _round_trip(original, TransformInput)
        assert decoded.changed_api_spec_file is not None
        assert (
            decoded.changed_api_spec_file.local_path
            == "/tmp/diff/changed_api_spec.jsonl"
        )
        assert decoded.changed_api_path_file is not None
        assert decoded.connection is not None
        assert decoded.connection.qualified_name == "default/api/conn"
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
