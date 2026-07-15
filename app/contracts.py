"""Data contracts for the OpenAPI Connector App.

All contracts extend Input/Output base classes for type safety and
backwards compatibility. Each App and @task method uses single-dataclass
inputs and outputs to ensure Temporal serialization works correctly.
"""

from typing import Annotated

from application_sdk.app import Input, Output
from application_sdk.contracts.base import PublishInputMixin
from application_sdk.contracts.types import ConnectionRef, FileReference, MaxItems
from application_sdk.credentials.ref import CredentialRef

from app.generated._input import AppInputContract

# =============================================================================
# App-level contracts
# =============================================================================


class PublishInput(Input):
    """Input for the publish-app child workflow (platform service).

    Matches the PublishAppConfig Pydantic model in atlan-publish-app.
    publish-app authenticates via platform-level OAuth2 env vars — no
    credential reference is needed here.
    """

    connection_qualified_name: str = ""
    transformed_data_prefix: str = ""
    publish_state_prefix: str = ""
    current_state_prefix: str = ""
    connection_creation_enabled: bool = True
    executor_enabled: bool = True
    connection_entity: ConnectionRef | None = None


OpenAPIConnectorInput = AppInputContract


class OpenAPIConnectorOutput(PublishInputMixin, Output):
    """Output from the OpenAPI Connector App.

    Mixes in ``PublishInputMixin`` so ``publish_state_prefix``,
    ``staging_data_prefix``, and ``current_state_prefix`` are auto-derived
    from ``connection_qualified_name`` and always present in the serialized
    output — the publish node's manifest args read them via
    ``$.extract.outputs.*`` JSONPath, which fails outright (not just with an
    empty value) if the key is absent entirely.
    """

    # Redeclared explicitly (not just inherited from PublishInputMixin): B005's
    # ledger checker is a single-class AST scanner that doesn't resolve fields
    # across base classes. A field visible only via inheritance reads as
    # "removed" if already ledger-tracked, and is silently invisible to the
    # ledger generator (never gets tracked) otherwise — so every mixin field
    # this contract relies on must be redeclared here to stay B005-protected
    # against a future accidental removal (the exact bug this mixin fixes).
    connection_qualified_name: str = ""
    transformed_data_prefix: str = ""
    publish_state_prefix: str = ""
    staging_data_prefix: str = ""
    current_state_prefix: str = ""

    api_spec_count: int = 0
    """Number of APISpec entities extracted (always 0 or 1)."""

    api_path_count: int = 0
    """Number of APIPath entities extracted."""

    output_file: FileReference | None = None
    """FileReference for the output JSONL file."""

    total_scanned: int = 0
    """Total number of assets scanned."""

    publish_completed: bool = False
    """True if the transformed file was uploaded to object storage."""

    assertion_only_enabled: bool = False
    """True when connection_usage=REUSE, signalling publish-app to run in
    assertion-only mode: forward the transformed rows as pure upserts with no
    diff and no archival. Read by the publish node via
    ``$.extract.outputs.assertion_only_enabled`` (CONNECT-55)."""


# =============================================================================
# Task-level contracts: extract_spec
# =============================================================================


class ExtractSpecInput(Input):
    """Input for the extract_spec task."""

    spec_url: str = ""
    """URL to fetch the OpenAPI spec from."""

    connection_qualified_name: str = ""
    """Atlan connection QN — used to pre-compute APISpec and APIPath QNs."""

    openapi_credential: CredentialRef | None = None
    """Optional credential for private specs (provides auth_header)."""


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
# Task-level contracts: download_cloud_spec
# =============================================================================


class DownloadCloudSpecInput(Input):
    """Input for the download_cloud_spec task."""

    cloud_source: str = ""
    """Cloud storage credential GUID."""

    spec_prefix: str = ""
    """Object store key prefix for cloud spec discovery."""

    spec_key: str = ""
    """Object key (filename) in the object store."""


class DownloadCloudSpecOutput(Output):
    """Output from the download_cloud_spec task."""

    spec_files: Annotated[list[FileReference], MaxItems(1000)] = []
    """FileReferences for the downloaded spec files."""


# =============================================================================
# Task-level contracts: transform
# =============================================================================


class TransformInput(Input):
    """Input for the transform task."""

    api_spec_file: FileReference | None = None
    """FileReference to api_spec records from extract_spec."""

    api_path_file: FileReference | None = None
    """FileReference to api_path records from extract_spec."""

    connection: ConnectionRef | None = None
    """Atlan connection reference. Always set by the workflow before calling transform.
    On the CREATE path the Connection entity is emitted first so the diff engine
    does not archive it (see ``emit_connection``)."""

    emit_connection: bool = True
    """Whether to emit the Connection entity as the first output row (CONNECT-55).
    True on CREATE (the connection is being created and must lead the diff).
    False on REUSE, where the connection already exists and must not be
    re-upserted/modified — only its child assets are emitted."""

    connection_qualified_name: str = ""
    """Connection qualified name — derived from connection.attributes.qualified_name.
    Kept for backwards-compatible Temporal serialization."""

    workflow_id: str = ""
    """Temporal workflow ID."""

    workflow_type: str = ""
    """Temporal workflow type / app name."""

    workflow_run_at_ms: int = 0
    """Workflow start time as millisecond-precision UNIX timestamp."""


class TransformOutput(Output):
    """Output from the transform task."""

    output_file: FileReference | None = None
    """FileReference to the final Atlan assets JSONL file."""

    api_spec_count: int = 0
    """Number of APISpec assets written."""

    api_path_count: int = 0
    """Number of APIPath assets written."""
