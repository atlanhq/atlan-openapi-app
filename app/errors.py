"""Domain-specific AppError subclasses for the OpenAPI connector.

Each class overrides ``code`` so that dashboard buckets are scoped to this
connector, not to the broad category leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from application_sdk.errors import (
    AppPermissionDeniedError,
    AuthError,
    DependencyUnavailableError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    SourceUnavailableError,
)


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
class SpecUrlInvalidError(InvalidInputError):
    """spec_url is not an allowed HTTPS URL."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_SPEC_URL_INVALID"


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

    code: ClassVar[str] = (
        "DEPENDENCY_UNAVAILABLE_OPENAPI_TENANT_OBJECTSTORE_UNAVAILABLE"
    )


# ---------------------------------------------------------------------------
# Spec-fetch classification (CONNECT-812, PF-20/EP-02 class).
#
# Every failure on the spec-fetch path must cross the activity boundary as a
# typed AppError carrying FailureDetails — a raw httpx/yaml exception is
# unattributable (no audience, no code) and reaches the customer as a stack
# trace. One leaf per failure class so dashboard buckets stay meaningful.
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class SpecFetchAuthError(AuthError):
    """The spec endpoint rejected the request as unauthenticated (HTTP 401)."""

    code: ClassVar[str] = "AUTH_OPENAPI_SPEC_FETCH"


@dataclass(kw_only=True)
class SpecFetchForbiddenError(AppPermissionDeniedError):
    """The spec endpoint refused access to the document (HTTP 403)."""

    code: ClassVar[str] = "PERMISSION_OPENAPI_SPEC_FETCH"


@dataclass(kw_only=True)
class SpecNotFoundError(NotFoundError):
    """The spec URL does not resolve to a document (HTTP 404)."""

    code: ClassVar[str] = "NOT_FOUND_OPENAPI_SPEC"


@dataclass(kw_only=True)
class SpecFetchRateLimitedError(RateLimitedError):
    """The spec endpoint throttled the request (HTTP 429)."""

    code: ClassVar[str] = "RATE_LIMITED_OPENAPI_SPEC_FETCH"


@dataclass(kw_only=True)
class SpecSourceUnavailableError(SourceUnavailableError):
    """The spec endpoint is unreachable, erroring (5xx), or not answering."""

    code: ClassVar[str] = "SOURCE_UNAVAILABLE_OPENAPI_SPEC"


@dataclass(kw_only=True)
class SpecFetchClientError(InvalidInputError):
    """The spec endpoint rejected the request with an unclassified 4xx."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_SPEC_FETCH"


@dataclass(kw_only=True)
class SpecRedirectNotFollowedError(InvalidInputError):
    """The spec endpoint answered with a redirect, which we deliberately do not
    follow.

    Redirects are disabled on the fetch client because a redirect target is
    outside the reach of :func:`app.api_client.validate_spec_url` — following one
    would let a public hostname bounce the request onto a private address. The
    distinct code exists so this reads as "point spec_url at the final document"
    rather than as a generic 4xx.
    """

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_SPEC_REDIRECT"


@dataclass(kw_only=True)
class SpecSourceTransientError(DependencyUnavailableError):
    """The spec endpoint could not be evaluated *this time* — 5xx, or reached
    but not answering.

    Deliberately a ``DEPENDENCY_UNAVAILABLE`` leaf and deliberately distinct from
    :class:`SpecSourceUnavailableError`. Raised (never returned as a verdict) from
    the preflight probe: the gate routes this category to fail-open, so a blip at
    the spec host can never abort a healthy run once the app opts into hard mode.
    The extraction path keeps the USER-audience ``SpecSourceUnavailableError`` for
    the same conditions, because there the run really did fail.
    """

    code: ClassVar[str] = "DEPENDENCY_UNAVAILABLE_OPENAPI_SPEC_SOURCE"


@dataclass(kw_only=True)
class SpecParseError(InvalidInputError):
    """The fetched document is not parseable as an OpenAPI JSON/YAML object."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_SPEC_PARSE"


@dataclass(kw_only=True)
class NoValidSpecsError(InvalidInputError):
    """Every fetched document was skipped — nothing extractable (EP-03 guard)."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_NO_VALID_SPECS"


@dataclass(kw_only=True)
class CloudSpecNotFoundError(NotFoundError):
    """The object-store prefix/key yielded no spec files (EP-03 guard)."""

    code: ClassVar[str] = "NOT_FOUND_OPENAPI_CLOUD_SPEC"


@dataclass(kw_only=True)
class ObjectStoreCredentialError(InvalidInputError):
    """The resolved object-store credential was rejected while building the
    cloud store client (PF-17 boundary — raised with a severed cause chain)."""

    code: ClassVar[str] = "INVALID_INPUT_OPENAPI_OBJECT_STORE_CREDENTIAL"
