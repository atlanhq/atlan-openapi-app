# AUTO-GENERATED from contract/app.pkl — DO NOT EDIT MANUALLY.
# To regenerate: pkl eval -m . contract/app.pkl
from __future__ import annotations

from typing import ClassVar

from application_sdk.credentials.ref import CredentialRef
from application_sdk.templates.contracts import ExtractionInput


class AppInputContract(ExtractionInput):
    _config_hash_exclude: ClassVar[set[str]] = {
        "output_dir",
        "checkpoint_dir",
        "load_to_atlan",
        "publish_dry_run",
    }

    import_type: str = "URL"
    """Select how you want to provide the OpenAPI spec file to be imported."""
    spec_url: str = ""
    """Full URL to the JSON form of the OpenAPI specification."""
    spec_prefix: str = ""
    """Enter the directory (path) within the object store from which to retrieve the OpenAPI spec file."""
    spec_key: str = ""
    """Enter the object key (filename), including its extension, within the object store and prefix."""
    connection_usage: str = "REUSE"
    """Whether to create a new connection to hold these API assets, or reuse an existing connection."""
    connection_qualified_name: str = ""
    """Select an existing connection to load assets into."""
    openapi_credential: CredentialRef | None = None
    output_dir: str = ""
    """Directory for output JSONL files."""
    checkpoint_dir: str = ""
    """Directory for checkpoint database. If provided, enables incremental extraction."""
    load_to_atlan: bool = True
    """If True, load extracted metadata to Atlan via publish-app."""
    publish_dry_run: bool = False
    """When True, skip the Atlas publish step (executor_enabled=False)."""
