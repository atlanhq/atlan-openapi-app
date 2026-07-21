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
class SourceCredentialRequiredError(InvalidInputError):
    """A source credential is required but was not supplied.

    The spec source (URL vs object store) is now selected entirely by the
    ``authType`` of the resolved source credential, so a credential is always
    required — even a public URL is expressed as an ``authType="url"``
    credential that carries ``spec_url`` (and no secret)."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_SOURCE_CREDENTIAL_REQUIRED"


@dataclass(kw_only=True)
class UnknownImportTypeError(InvalidInputError):
    """The resolved credential's ``authType`` is not a recognised value."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_UNKNOWN_IMPORT_TYPE"


@dataclass(kw_only=True)
class TenantObjectStoreUnavailableError(DependencyUnavailableError):
    """The Dapr objectstore binding is not configured on this deployment."""

    code: ClassVar[str] = (
        "DEPENDENCY_UNAVAILABLE_OPENAPI_TENANT_OBJECTSTORE_UNAVAILABLE"
    )
