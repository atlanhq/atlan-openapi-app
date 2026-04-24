# AUTO-GENERATED from app.pkl — DO NOT EDIT MANUALLY.
# To regenerate: make generate
from __future__ import annotations
from typing import ClassVar
from application_sdk.contracts.base import Input
from application_sdk.contracts.types import ConnectionRef


class AppInputContract(Input):
    _config_hash_exclude: ClassVar[set[str]] = {
        "output_dir",
        "checkpoint_dir",
        "load_to_atlan",
        "publish_dry_run",
    }

    import_type: str = "URL"
    """How to provide the spec: 'URL' (HTTP fetch) or 'CLOUD' (object storage)."""
    spec_url: str = ""
    """URL to the OpenAPI spec JSON/YAML document. Required when import_type='URL'."""
    spec_prefix: str = ""
    """Object store directory path. Required when import_type='CLOUD'."""
    spec_key: str = ""
    """Object key (filename) in the object store. Required when import_type='CLOUD'."""
    cloud_source: str = ""
    """Cloud storage credential (csa-connectors-objectstore). Required when import_type='CLOUD'."""
    connection: ConnectionRef | None = None
    """Atlan connection to create or reuse."""
    output_dir: str = ""
    """Directory for output JSONL files."""
    checkpoint_dir: str = ""
    """Directory for checkpoint database. If provided, enables incremental extraction."""
    load_to_atlan: bool = True
    """If True, load extracted metadata to Atlan via publish-app."""
    publish_dry_run: bool = False
    """When True, skip the Atlas publish step (executor_enabled=False)."""
