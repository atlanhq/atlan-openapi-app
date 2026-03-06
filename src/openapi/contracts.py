"""Data contracts for the OpenAPI Connector App.

All contracts extend Input/Output base classes for type safety and
backwards compatibility. Each App and @task method uses single-dataclass
inputs and outputs to ensure Temporal serialization works correctly.
"""

from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from app_framework.app import Input, MaxItems, Output
from app_framework.app.types import FileReference
from app_framework.credentials import CredentialRef
from atlan_loader.contracts import LoadError
from pyatlan.models.connection import Connection

# =============================================================================
# App-level contracts
# =============================================================================


@dataclass
class OpenAPIConnectorInput(Input):
    """Input for the OpenAPI Connector App.

    Extracts metadata from an OpenAPI spec document (JSON or YAML) accessible
    via a URL. Creates one APISpec and N APIPath entities in Atlan.
    """

    _config_hash_exclude: ClassVar[set[str]] = {
        "output_dir",
        "checkpoint_dir",
        "load_to_atlan",
        "atlan_credential",
        "loader_batch_size",
        "loader_chunk_size",
        "loader_max_chunks_per_execution",
        "loader_save_timeout",
        "loader_dry_run",
    }

    # === Required ===
    connection: Connection | None = None
    """Atlan connection object. Required. Provides qualified_name and name."""

    import_type: str = "URL"
    """How to provide the spec: 'URL' (HTTP fetch), 'CLOUD' (object storage presigned URL).
    'DIRECT' (UI file upload) is not supported in the App Framework."""

    spec_url: str = ""
    """URL to the OpenAPI spec JSON/YAML document. Required when import_type='URL'.
    For import_type='CLOUD', use the presigned URL here."""

    # === Optional credential (for private specs with auth) ===
    openapi_credential: CredentialRef | None = None
    """Optional credential for private OpenAPI specs. Not needed for public specs."""

    # === Output ===
    output_dir: str = ""
    """Directory for output JSONL files. Defaults to /tmp/openapi/{run_id}."""

    # === Change detection ===
    checkpoint_dir: str = ""
    """Directory for checkpoint database. If provided, enables incremental extraction."""

    # === Atlan loading ===
    load_to_atlan: bool = False
    """If True, load extracted metadata to Atlan via atlan-loader child app."""

    atlan_credential: CredentialRef | None = None
    """Credential for Atlan API authentication. Required if load_to_atlan=True."""

    loader_batch_size: int = 20
    """Assets per save() call when loading to Atlan."""

    loader_chunk_size: int = 10000
    """Records per chunk when loading to Atlan."""

    loader_max_chunks_per_execution: int = 500
    """Chunks to process before continue-as-new in atlan-loader."""

    loader_save_timeout: float | None = None
    """Per-request timeout in seconds for atlan-loader save() calls."""

    loader_dry_run: bool = False
    """When True, validate assets instead of loading."""


@dataclass
class OpenAPIConnectorOutput(Output):
    """Output from the OpenAPI Connector App."""

    api_spec_count: int = 0
    """Number of APISpec entities extracted (always 0 or 1)."""

    api_path_count: int = 0
    """Number of APIPath entities extracted."""

    output_file: FileReference | None = None
    """FileReference for the output JSONL file."""

    total_scanned: int = 0
    """Total number of assets scanned (before change detection)."""

    new_count: int = 0
    """Number of NEW assets."""

    changed_count: int = 0
    """Number of CHANGED assets."""

    unchanged_count: int = 0
    """Number of UNCHANGED assets (skipped from output)."""

    deleted_count: int = 0
    """Number of DELETED assets."""

    checkpoint_ref: FileReference | None = None
    """Reference to the checkpoint file in durable storage."""

    atlan_loaded_count: int = 0
    atlan_created_count: int = 0
    atlan_updated_count: int = 0
    atlan_validated_count: int = 0
    atlan_error_count: int = 0
    atlan_errors: Annotated[list[LoadError], MaxItems(100)] = field(default_factory=list)


# =============================================================================
# Task-level contracts: extract_spec
# =============================================================================


@dataclass
class ExtractSpecInput(Input):
    """Input for the extract_spec task."""

    spec_url: str = ""
    """URL to fetch the OpenAPI spec from."""

    connection_qualified_name: str = ""
    """Atlan connection QN — used to pre-compute APISpec and APIPath QNs."""

    output_dir: str = ""
    """Directory for raw output JSONL files."""

    openapi_credential: CredentialRef | None = None
    """Optional credential for private specs (provides auth_header)."""


@dataclass
class ExtractSpecOutput(Output):
    """Output from the extract_spec task."""

    api_spec_file: FileReference | None = None
    """FileReference to api_spec.jsonl (1 record)."""

    api_path_file: FileReference | None = None
    """FileReference to api_path.jsonl (N records)."""

    api_spec_count: int = 0
    """Number of APISpec records extracted (always 1 on success)."""

    api_path_count: int = 0
    """Number of APIPath records extracted."""


# =============================================================================
# Task-level contracts: diff
# =============================================================================


@dataclass
class DiffInput(Input):
    """Input for the diff task."""

    api_spec_file: FileReference | None = None
    """FileReference to api_spec.jsonl from extract_spec."""

    api_path_file: FileReference | None = None
    """FileReference to api_path.jsonl from extract_spec."""

    connection: Connection | None = None
    """Atlan connection object. Provides the qualified name for change detection keys."""

    checkpoint_dir: str = ""
    """Directory for checkpoint database."""

    output_dir: str = ""
    """Directory for changed-records output files."""


@dataclass
class DiffOutput(Output):
    """Output from the diff task."""

    changed_api_spec_file: FileReference | None = None
    """FileReference to changed api_spec records."""

    changed_api_path_file: FileReference | None = None
    """FileReference to changed api_path records."""

    new_count: int = 0
    changed_count: int = 0
    unchanged_count: int = 0
    deleted_count: int = 0
    total_scanned: int = 0

    checkpoint_new_path: str = ""
    """Path to the staged (uncommitted) checkpoint directory."""


# =============================================================================
# Task-level contracts: transform
# =============================================================================


@dataclass
class TransformInput(Input):
    """Input for the transform task."""

    changed_api_spec_file: FileReference | None = None
    """FileReference to (changed) api_spec records."""

    changed_api_path_file: FileReference | None = None
    """FileReference to (changed) api_path records."""

    connection: Connection | None = None
    """Atlan connection object. Provides the qualified name and display name."""

    output_dir: str = ""
    """Directory for output file."""

    workflow_id: str = ""
    """Temporal workflow ID."""

    workflow_type: str = ""
    """Temporal workflow type / app name."""

    workflow_run_at_ms: int = 0
    """Workflow start time as millisecond-precision UNIX timestamp."""


@dataclass
class TransformOutput(Output):
    """Output from the transform task."""

    output_file: FileReference | None = None
    """FileReference to the final Atlan assets JSONL file."""

    api_spec_count: int = 0
    """Number of APISpec assets written."""

    api_path_count: int = 0
    """Number of APIPath assets written."""
