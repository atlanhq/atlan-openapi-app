"""Asset mapping utilities for the OpenAPI Connector.

Maps typed OpenAPI extracted records to pyatlan Asset models and generates
qualified names following Atlan conventions.

Qualified Name Formats (from SPEC.md §4):
    Connection: provided as connection.qualified_name  (e.g. default/api/1234567)
    APISpec:    {connection_qn}/{spec_title}
    APIPath:    {spec_qn}{path_url}   (path_url starts with '/', so concat directly)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openapi.api_types import OpenAPIPathRecord, OpenAPISpecRecord

from app_framework.atlan import apply_sync_metadata
from pyatlan_v9.model.assets import APIPath, APISpec, Connection, RelatedAPISpec

# Connector name constant — matches connector_name in the Connection asset
CONNECTOR_NAME = "api"


# =============================================================================
# Qualified Name Builders
# =============================================================================


def build_api_spec_qn(connection_qn: str, spec_title: str) -> str:
    """Build qualified name for an APISpec asset.

    Args:
        connection_qn: Atlan connection qualified name.
        spec_title: The spec title from spec['info']['title'].
    """
    return f"{connection_qn}/{spec_title}"


def build_api_path_qn(spec_qn: str, path_url: str) -> str:
    """Build qualified name for an APIPath asset.

    path_url already starts with '/' so concat directly (no extra slash).

    Args:
        spec_qn: Parent APISpec qualified name.
        path_url: The path URL key from spec['paths'], e.g. '/pet/{petId}'.
    """
    return f"{spec_qn}{path_url}"


# =============================================================================
# Asset Mappers
# =============================================================================


def map_connection(connection: Connection) -> Connection:
    """Re-emit the Connection as an upsertable entity for the Atlan Loader.

    Args:
        connection: The Connection object provided by the caller (from input.connection).
            Its qualified_name and name are authoritative.
    """
    _qn = connection.qualified_name
    conn_qn: str = _qn if isinstance(_qn, str) else ""
    _name = connection.name
    conn_name: str = (
        _name if isinstance(_name, str) and _name else conn_qn.rsplit("/", 1)[-1]
    )
    return Connection(
        qualified_name=conn_qn,
        name=conn_name,
        connector_name=CONNECTOR_NAME,
        connection_qualified_name=conn_qn,
        category="API",
    )


def map_api_spec(
    record: "OpenAPISpecRecord",
    connection_qn: str,
    workflow_id: str,
    workflow_type: str,
    workflow_run_at_ms: int,
) -> APISpec:
    """Create an APISpec Atlan asset from an OpenAPISpecRecord.

    Args:
        record: Extracted spec metadata from the OpenAPI info block.
        connection_qn: Atlan connection qualified name (base for QN).
        workflow_id: Temporal workflow ID.
        workflow_type: Temporal workflow type / app name.
        workflow_run_at_ms: Workflow start time as millisecond UNIX timestamp.
    """
    spec_qn = build_api_spec_qn(connection_qn, record.title)

    asset = APISpec(
        qualified_name=spec_qn,
        name=record.title,
        connector_name=CONNECTOR_NAME,
        connection_qualified_name=connection_qn,
    )

    if record.spec_url:
        asset.source_url = record.spec_url
    if record.openapi_version:
        asset.api_spec_type = record.openapi_version
    if record.description:
        asset.description = record.description
    if record.terms_of_service:
        asset.api_spec_terms_of_service_url = record.terms_of_service
    if record.spec_version:
        asset.api_spec_version = record.spec_version

    contact = record.contact_email or record.contact_name or record.contact_url
    if contact:
        if record.contact_email:
            asset.api_spec_contact_email = record.contact_email
        if record.contact_name:
            asset.api_spec_contact_name = record.contact_name
        if record.contact_url:
            asset.api_spec_contact_url = record.contact_url

    license_info = record.license_name or record.license_url
    if license_info:
        if record.license_name:
            asset.api_spec_license_name = record.license_name
        if record.license_url:
            asset.api_spec_license_url = record.license_url

    external_docs = record.external_docs_url or record.external_docs_description
    if external_docs:
        docs: dict[str, str] = {}
        if record.external_docs_url:
            docs["url"] = record.external_docs_url
        if record.external_docs_description:
            docs["description"] = record.external_docs_description
        asset.api_external_docs = docs

    apply_sync_metadata(asset, workflow_id, workflow_type, workflow_run_at_ms)
    return asset


def map_api_path(
    record: "OpenAPIPathRecord",
    connection_qn: str,
    workflow_id: str,
    workflow_type: str,
    workflow_run_at_ms: int,
) -> APIPath:
    """Create an APIPath Atlan asset from an OpenAPIPathRecord.

    Args:
        record: Extracted path metadata from spec['paths'].
        connection_qn: Atlan connection qualified name.
        workflow_id: Temporal workflow ID.
        workflow_type: Temporal workflow type / app name.
        workflow_run_at_ms: Workflow start time as millisecond UNIX timestamp.
    """
    spec_qn = record.spec_qualified_name
    path_qn = build_api_path_qn(spec_qn, record.path_url)

    asset = APIPath(
        qualified_name=path_qn,
        name=record.path_url,
        connector_name=CONNECTOR_NAME,
        connection_qualified_name=connection_qn,
        api_spec_name=record.spec_title,
        api_spec_qualified_name=spec_qn,
        api_spec=RelatedAPISpec(qualified_name=spec_qn),
        api_path_raw_uri=record.path_url,
        api_path_is_templated=record.is_templated,
    )

    if record.summary:
        asset.api_path_summary = record.summary
    if record.available_operations:
        asset.api_path_available_operations = record.available_operations
    if record.description:
        asset.description = record.description

    apply_sync_metadata(asset, workflow_id, workflow_type, workflow_run_at_ms)
    return asset
