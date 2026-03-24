"""Data contracts for the OpenAPI Connector App.

All contracts extend Input/Output base classes for type safety and
backwards compatibility. Each App and @task method uses single-dataclass
inputs and outputs to ensure Temporal serialization works correctly.
"""

from application_sdk.app import Input, Output
from application_sdk.contracts.types import ConnectionRef, FileReference
from application_sdk.credentials.ref import CredentialRef

from app.contract.generated.input import AppInputContract

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
    connection_entity: dict = {}


class OpenAPIConnectorInput(AppInputContract):
    """Input for the OpenAPI Connector App.

    Extends the Pkl-generated AppInputContract with app-specific fields
    not derivable from the UI config.
    """

    load_to_atlan: bool = False
    """If True, load extracted metadata to Atlan via publish-app."""

    openapi_credential: CredentialRef | None = None
    """Optional credential for private OpenAPI specs. Not needed for public specs."""


class OpenAPIConnectorOutput(Output):
    """Output from the OpenAPI Connector App."""

    api_spec_count: int = 0
    """Number of APISpec entities extracted (always 0 or 1)."""

    api_path_count: int = 0
    """Number of APIPath entities extracted."""

    output_file: FileReference | None = None
    """FileReference for the output JSONL file."""

    total_scanned: int = 0
    """Total number of assets scanned."""

    atlan_loaded_count: int = 0
    atlan_created_count: int = 0
    atlan_updated_count: int = 0
    atlan_validated_count: int = 0
    atlan_error_count: int = 0
    atlan_errors: list = []

    publish_completed: bool = False
    """True if publish-app was called and completed successfully."""


# =============================================================================
# Task-level contracts: extract_spec
# =============================================================================


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
# Task-level contracts: transform
# =============================================================================


class TransformInput(Input):
    """Input for the transform task."""

    api_spec_file: FileReference | None = None
    """FileReference to api_spec records from extract_spec."""

    api_path_file: FileReference | None = None
    """FileReference to api_path records from extract_spec."""

    connection: ConnectionRef | None = None
    """Atlan connection reference (CREATE path). When set, the Connection entity is emitted
    in the output. When None (REUSE path), connection_qualified_name is used for QN
    derivation and no Connection entity is written."""

    connection_qualified_name: str = ""
    """Connection QN (REUSE path). Used for APISpec/APIPath QN derivation when
    connection is None."""

    output_dir: str = ""
    """Directory for output file."""

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
