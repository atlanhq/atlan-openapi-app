"""Typed dataclasses for OpenAPI connector extracted records.

These types are serialized to/from JSONL via msgspec and used directly
with the ChangeDetector. One dataclass per entity type from SPEC.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OpenAPISpecRecord:
    """Extracted metadata from the OpenAPI spec info block (one per spec fetch).

    Source: spec['info'] + root-level fields.
    Used to create one APISpec Atlan entity per run.
    """

    title: str
    """spec['info']['title'] — used as APISpec name and QN component."""

    openapi_version: str = ""
    """spec['openapi'] — e.g. '3.0.4'."""

    description: str = ""
    """spec['info']['description']."""

    terms_of_service: str = ""
    """spec['info']['termsOfService']."""

    contact_name: str = ""
    """spec['info']['contact']['name']."""

    contact_email: str = ""
    """spec['info']['contact']['email']."""

    contact_url: str = ""
    """spec['info']['contact']['url']."""

    license_name: str = ""
    """spec['info']['license']['name']."""

    license_url: str = ""
    """spec['info']['license']['url']."""

    spec_version: str = ""
    """spec['info']['version']."""

    external_docs_url: str = ""
    """spec['externalDocs']['url']."""

    external_docs_description: str = ""
    """spec['externalDocs']['description']."""

    spec_url: str = ""
    """The URL used to fetch the spec — stored as source_url on the APISpec asset."""


@dataclass
class OpenAPIPathRecord:
    """One path entry from spec['paths'] (one record per path key).

    Source: Each key-value pair in spec['paths'].
    Used to create one APIPath Atlan entity per record.
    """

    path_url: str
    """The path dict key, e.g. '/pet/{petId}'. Used as name and api_path_raw_uri."""

    spec_title: str
    """Parent spec title (for QN computation in diff and transform)."""

    spec_qualified_name: str
    """Pre-computed parent APISpec QN: f'{connection_qn}/{spec_title}'.
    Stored here so diff and transform can avoid recomputing it."""

    summary: str = ""
    """path_item.get('summary')."""

    available_operations: list[str] = field(default_factory=list)
    """Uppercase HTTP methods present on this path (GET, POST, PUT, PATCH, DELETE)."""

    description: str = ""
    """Markdown table of operations constructed from path_item methods."""

    is_templated: bool = False
    """True if '{' and '}' both appear in path_url."""
