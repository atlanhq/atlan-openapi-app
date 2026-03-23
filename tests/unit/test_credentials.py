# TODO(v3-migration): several tests need manual updates:
# - test_frozen_dataclass: OpenAPICredential is no longer a frozen dataclass; mutation won't raise
# - test_validate_always_succeeds / test_validate_succeeds_with_auth_header: validate() now returns
#   None instead of a ValidationResult object — assertions on result.success will fail
# - TestAutoRegistration methods: CredentialTypeRegistry no longer has get_instance(); use
#   CredentialTypeRegistry() directly; registry.get() → registry.get_class(); list_types() → registered_types()
"""Unit tests for OpenAPI credential parsing and factory functions."""

from application_sdk.credentials.registry import CredentialTypeRegistry
from app.credentials import (
    OpenAPICredential,
    _parse_openapi_credential,
    openapi_credential_ref,
)


class TestParseOpenAPICredential:
    def test_parses_auth_header(self) -> None:
        data = {"type": "openapi", "auth_header": "Bearer test-token-123"}
        cred = _parse_openapi_credential(data)
        assert isinstance(cred, OpenAPICredential)
        assert cred.auth_header == "Bearer test-token-123"

    def test_default_auth_header_empty(self) -> None:
        """auth_header defaults to empty string when not provided."""
        data = {"type": "openapi"}
        cred = _parse_openapi_credential(data)
        assert cred.auth_header == ""

    def test_empty_dict_gives_defaults(self) -> None:
        """An empty dict is valid — OpenAPI credential has no required fields."""
        cred = _parse_openapi_credential({})
        assert cred.auth_header == ""

    def test_credential_type_property(self) -> None:
        cred = OpenAPICredential(auth_header="Bearer abc")
        assert cred.credential_type == "openapi"

    def test_mutable(self) -> None:
        """OpenAPICredential is a plain class — mutation is allowed."""
        cred = OpenAPICredential(auth_header="Bearer abc")
        cred.auth_header = "Bearer new-token"
        assert cred.auth_header == "Bearer new-token"

    async def test_validate_always_succeeds(self) -> None:
        """validate() always returns None (public specs need no auth check)."""
        cred = OpenAPICredential()
        result = await cred.validate()
        assert result is None

    async def test_validate_succeeds_with_auth_header(self) -> None:
        cred = OpenAPICredential(auth_header="Bearer some-token")
        result = await cred.validate()
        assert result is None


class TestOpenAPICredentialRef:
    def test_creates_ref_with_correct_name(self) -> None:
        ref = openapi_credential_ref("my-openapi-cred")
        assert ref.name == "my-openapi-cred"

    def test_creates_ref_with_openapi_type(self) -> None:
        ref = openapi_credential_ref("test")
        assert ref.credential_type == "openapi"

    def test_default_store_name(self) -> None:
        ref = openapi_credential_ref("test")
        assert ref.store_name == "default"

    def test_custom_store_name(self) -> None:
        ref = openapi_credential_ref("test", store_name="vault")
        assert ref.store_name == "vault"


class TestAutoRegistration:
    def test_openapi_type_is_registered(self) -> None:
        """Importing credentials module should auto-register 'openapi' type."""
        registry = CredentialTypeRegistry()
        cls = registry.get_class("openapi")
        assert cls is OpenAPICredential

    def test_registered_parser_works(self) -> None:
        registry = CredentialTypeRegistry()
        cred = registry.parse("openapi", {"auth_header": "Bearer registered-test"})
        assert isinstance(cred, OpenAPICredential)
        assert cred.auth_header == "Bearer registered-test"

    def test_type_in_type_list(self) -> None:
        registry = CredentialTypeRegistry()
        assert "openapi" in registry.registered_types()
