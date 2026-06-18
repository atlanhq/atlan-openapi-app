"""Domain-specific AppError subclasses for the OpenAPI connector.

Each class overrides ``code`` so that dashboard buckets are scoped to this
connector, not to the broad category leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from application_sdk.errors import DependencyUnavailableError, InvalidInputError


@dataclass(kw_only=True)
class ZipNoSpecFoundError(InvalidInputError):
    """No valid OpenAPI spec found in the downloaded ZIP archive."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_ZIP_NO_SPEC"


@dataclass(kw_only=True)
class ConnectionRequiredError(InvalidInputError):
    """The connection field is required but was not supplied."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_CONNECTION_REQUIRED"


@dataclass(kw_only=True)
class SpecUrlRequiredError(InvalidInputError):
    """spec_url is required but was not supplied."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_SPEC_URL_REQUIRED"


@dataclass(kw_only=True)
class CloudSpecLocationRequiredError(InvalidInputError):
    """spec_prefix or spec_key must be provided for CLOUD import_type."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_CLOUD_SPEC_LOCATION_REQUIRED"


@dataclass(kw_only=True)
class UnknownImportTypeError(InvalidInputError):
    """import_type is not a recognised value."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_UNKNOWN_IMPORT_TYPE"


@dataclass(kw_only=True)
class TenantObjectStoreUnavailableError(DependencyUnavailableError):
    """The Dapr objectstore binding is not configured on this deployment."""

    code: ClassVar[str] = "DEPENDENCY_UNAVAILABLE_OPENAPI_TENANT_OBJECTSTORE_UNAVAILABLE"
