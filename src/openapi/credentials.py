"""OpenAPI credential type.

This connector primarily handles public OpenAPI specs which require no
authentication. This credential type supports optional auth (e.g. a Bearer
token in the Authorization header) for private spec endpoints.

Usage:
    # Public spec (no auth needed) — credential not required at all.
    # For private specs with a Bearer token:
    {
        "type": "openapi",
        "auth_header": "Bearer <your-token>"
    }

    # Create credential references using factory function:
    ref = openapi_credential_ref("openapi")

Environment variable mapping (via run_dev.py):
    OPENAPI_AUTH_HEADER -> auth_header (optional)
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app_framework.credentials.ref import CredentialRef
from app_framework.credentials.registry import (
    CredentialTypeRegistry,
    register_credential_type,
)

if TYPE_CHECKING:
    from app_framework.app.base import StateAccessor
    from app_framework.credentials.types import ValidationResult

# Well-known key for handing the resolved auth header from validate() to
# extract_spec, avoiding a second DAPR credential lookup in the task.
VALIDATED_AUTH_HEADER_KEY = "_validated_openapi_auth_header"


@dataclass(frozen=True)
class OpenAPICredential:
    """Optional OpenAPI credential for private spec endpoints.

    Secret store format:
        {
            "type": "openapi",
            "auth_header": "Bearer <token>"  # optional
        }
    """

    auth_header: str = ""
    """HTTP Authorization header value for private spec endpoints.
    Example: 'Bearer my-token'. Empty string for public specs."""

    @property
    def credential_type(self) -> str:
        return "openapi"

    async def validate(
        self,
        state: "StateAccessor | None" = None,
    ) -> "ValidationResult":
        """Validate the OpenAPI credential and store auth_header in app state.

        Always succeeds — public specs need no auth. For private specs, stores
        the auth_header under VALIDATED_AUTH_HEADER_KEY so extract_spec can
        claim it without a second DAPR credential lookup.

        If state is None (standalone validation), nothing is stored.
        """
        from app_framework.credentials.types import ValidationResult

        if state is not None and self.auth_header:
            state.set(VALIDATED_AUTH_HEADER_KEY, self.auth_header)

        return ValidationResult(
            success=True,
            message="OpenAPI credential valid",
            identity="openapi:authenticated" if self.auth_header else "openapi:public",
        )


def _parse_openapi_credential(data: dict[str, Any]) -> OpenAPICredential:
    """Parse OpenAPICredential from JSON dict."""
    return OpenAPICredential(
        auth_header=data.get("auth_header", ""),
    )


def openapi_credential_ref(
    name: str,
    *,
    store_name: str = "default",
) -> CredentialRef:
    """Create a reference to an OpenAPI credential."""
    return CredentialRef(
        name=name,
        credential_type="openapi",
        store_name=store_name,
    )


def _register_openapi_credential_type() -> None:
    """Register OpenAPI credential type with the global registry."""
    registry = CredentialTypeRegistry.get_instance()
    if registry.get("openapi") is None:
        register_credential_type(
            "openapi", OpenAPICredential, _parse_openapi_credential
        )


# Register on import
_register_openapi_credential_type()
