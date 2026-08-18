"""Unit tests for OpenAPIHandler (FND-264).

Covers the SDK preflight gap this handler closes: previously this app had no
Handler at all and fell back to DefaultHandler (always READY), so a run could
only discover a bad spec_url credential or an unreachable spec host mid-
extraction. These tests verify preflight_check now catches both failure
shapes before extraction starts, for both import_type branches.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from app.handler import OpenAPIHandler
from application_sdk.handler import (
    AuthStatus,
    HandlerCredential,
    PreflightInput,
    PreflightStatus,
)
from application_sdk.handler.contracts import BaseConnectionConfig


def _preflight_input(**config_fields) -> PreflightInput:
    """Build a PreflightInput with the given connection_config fields.

    Mirrors what the injected gate builds from a raw model_dump of
    OpenAPIConnectorInput (app/contracts.py) -- see
    application_sdk.execution._temporal.preflight_gate._config_from_snapshot.
    """
    return PreflightInput(connection_config=BaseConnectionConfig(**config_fields))


# =============================================================================
# TestPreflightUrlImport
# =============================================================================


class TestPreflightUrlImport:
    @pytest.mark.asyncio
    @respx.mock
    async def test_reachable_spec_url_is_ready(self) -> None:
        """A 200 HEAD response on spec_url means the source is READY."""
        respx.head("https://example.com/openapi.json").mock(
            return_value=httpx.Response(200)
        )
        handler = OpenAPIHandler()
        result = await handler.preflight_check(
            _preflight_input(
                import_type="URL", spec_url="https://example.com/openapi.json"
            )
        )

        assert result.status == PreflightStatus.READY
        assert len(result.checks) == 1
        assert result.checks[0].passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_403_is_not_ready_with_source_auth_error(self) -> None:
        """A 403 on spec_url is a source-auth failure, not connectivity."""
        respx.head("https://example.com/openapi.json").mock(
            return_value=httpx.Response(403)
        )
        respx.get("https://example.com/openapi.json").mock(
            return_value=httpx.Response(403)
        )
        handler = OpenAPIHandler()
        result = await handler.preflight_check(
            _preflight_input(
                import_type="URL", spec_url="https://example.com/openapi.json"
            )
        )

        assert result.status == PreflightStatus.NOT_READY
        check = result.checks[0]
        assert check.passed is False
        assert check.error is not None
        assert check.error.code == "AUTH"
        assert "authenticate" in check.resolved_message.lower()
        assert "example.com/openapi.json" in check.resolved_message

    @pytest.mark.asyncio
    @respx.mock
    async def test_401_is_not_ready_with_source_auth_error(self) -> None:
        """A 401 on spec_url is also classified as source-auth."""
        respx.head("https://example.com/openapi.json").mock(
            return_value=httpx.Response(401)
        )
        respx.get("https://example.com/openapi.json").mock(
            return_value=httpx.Response(401)
        )
        handler = OpenAPIHandler()
        result = await handler.preflight_check(
            _preflight_input(
                import_type="URL", spec_url="https://example.com/openapi.json"
            )
        )

        assert result.status == PreflightStatus.NOT_READY
        assert result.checks[0].error.code == "AUTH"

    @pytest.mark.asyncio
    @respx.mock
    async def test_connection_error_is_not_ready_with_source_connectivity_error(
        self,
    ) -> None:
        """A transport-level failure (DNS, refused, timeout) is source
        connectivity, not auth."""
        respx.head("https://unreachable.example.com/openapi.json").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        handler = OpenAPIHandler()
        result = await handler.preflight_check(
            _preflight_input(
                import_type="URL",
                spec_url="https://unreachable.example.com/openapi.json",
            )
        )

        assert result.status == PreflightStatus.NOT_READY
        check = result.checks[0]
        assert check.passed is False
        assert check.error is not None
        assert check.error.code == "SOURCE_UNAVAILABLE"
        assert "could not reach" in check.resolved_message.lower()

    @pytest.mark.asyncio
    async def test_missing_spec_url_is_not_ready(self) -> None:
        """import_type='URL' with no spec_url is an invalid-input failure,
        not a network probe."""
        handler = OpenAPIHandler()
        result = await handler.preflight_check(_preflight_input(import_type="URL"))

        assert result.status == PreflightStatus.NOT_READY
        assert result.checks[0].error.code == "INVALID_INPUT"


# =============================================================================
# TestPreflightCloudImport
# =============================================================================


class TestPreflightCloudImport:
    @pytest.mark.asyncio
    async def test_valid_credential_and_reachable_store_is_ready(self) -> None:
        """A resolved credential with key-based auth and a reachable store
        (bounded list() succeeds) is READY."""
        creds = [
            HandlerCredential(key="authType", value="s3"),
            HandlerCredential(key="username", value="AKIAEXAMPLE"),
            HandlerCredential(key="password", value="secret"),
        ]
        handler = OpenAPIHandler()
        fake_store = AsyncMock()
        fake_store.list = AsyncMock(return_value=["specs/openapi.json"])
        with patch(
            "application_sdk.storage.cloud.CloudStore.from_credentials",
            return_value=fake_store,
        ):
            result = await handler.preflight_check(
                PreflightInput(
                    credentials=creds,
                    connection_config=BaseConnectionConfig(
                        import_type="CLOUD", spec_prefix="specs"
                    ),
                )
            )

        assert result.status == PreflightStatus.READY
        assert result.checks[0].passed is True
        fake_store.list.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_credential_is_not_ready(self) -> None:
        """A resolved credential with neither key- nor role-based auth is a
        source-auth failure -- no network call should even be attempted."""
        creds = [HandlerCredential(key="authType", value="s3")]
        handler = OpenAPIHandler()
        with patch(
            "application_sdk.storage.cloud.CloudStore.from_credentials"
        ) as from_credentials:
            result = await handler.preflight_check(
                PreflightInput(
                    credentials=creds,
                    connection_config=BaseConnectionConfig(import_type="CLOUD"),
                )
            )

        assert result.status == PreflightStatus.NOT_READY
        check = result.checks[0]
        assert check.error is not None
        assert check.error.code == "AUTH"
        from_credentials.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_external_credential_falls_back_to_tenant_store_and_is_ready(
        self,
    ) -> None:
        """No resolved credential at all means the tenant's own object store
        is used (download_cloud_spec's Path B) -- not itself a failure."""
        handler = OpenAPIHandler()
        result = await handler.preflight_check(
            PreflightInput(
                credentials=[],
                connection_config=BaseConnectionConfig(import_type="CLOUD"),
            )
        )

        assert result.status == PreflightStatus.READY

    @pytest.mark.asyncio
    async def test_unreachable_store_is_not_ready_with_source_connectivity_error(
        self,
    ) -> None:
        """A misconfigured/unreachable store surfaces as a failed check, not
        an unhandled exception."""
        from application_sdk.storage.errors import StorageError

        creds = [
            HandlerCredential(key="authType", value="s3"),
            HandlerCredential(key="username", value="AKIAEXAMPLE"),
            HandlerCredential(key="password", value="secret"),
        ]
        handler = OpenAPIHandler()
        fake_store = AsyncMock()
        fake_store.list = AsyncMock(side_effect=StorageError("bucket not found"))
        with patch(
            "application_sdk.storage.cloud.CloudStore.from_credentials",
            return_value=fake_store,
        ):
            result = await handler.preflight_check(
                PreflightInput(
                    credentials=creds,
                    connection_config=BaseConnectionConfig(import_type="CLOUD"),
                )
            )

        assert result.status == PreflightStatus.NOT_READY
        assert result.checks[0].error.code == "SOURCE_UNAVAILABLE"


# =============================================================================
# TestPreflightUnknownImportType
# =============================================================================


class TestPreflightUnknownImportType:
    @pytest.mark.asyncio
    async def test_unknown_import_type_is_not_ready(self) -> None:
        handler = OpenAPIHandler()
        result = await handler.preflight_check(_preflight_input(import_type="SFTP"))

        assert result.status == PreflightStatus.NOT_READY
        assert result.checks[0].error.code == "INVALID_INPUT"


# =============================================================================
# TestAuth
# =============================================================================


class TestAuth:
    @pytest.mark.asyncio
    async def test_no_credentials_passes_through(self) -> None:
        """URL-mode has no standalone credential to test against."""
        from application_sdk.handler import AuthInput

        handler = OpenAPIHandler()
        result = await handler.test_auth(AuthInput())

        assert result.status == AuthStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_valid_cloud_credential_succeeds(self) -> None:
        from application_sdk.handler import AuthInput

        creds = [
            HandlerCredential(key="authType", value="s3"),
            HandlerCredential(key="username", value="AKIAEXAMPLE"),
            HandlerCredential(key="password", value="secret"),
        ]
        handler = OpenAPIHandler()
        fake_store = AsyncMock()
        fake_store.list = AsyncMock(return_value=[])
        with patch(
            "application_sdk.storage.cloud.CloudStore.from_credentials",
            return_value=fake_store,
        ):
            result = await handler.test_auth(AuthInput(credentials=creds))

        assert result.status == AuthStatus.SUCCESS

    @pytest.mark.asyncio
    async def test_invalid_cloud_credential_fails(self) -> None:
        from application_sdk.handler import AuthInput

        creds = [HandlerCredential(key="authType", value="s3")]
        handler = OpenAPIHandler()
        result = await handler.test_auth(AuthInput(credentials=creds))

        assert result.status == AuthStatus.INVALID_CREDENTIALS


# =============================================================================
# TestFetchMetadata
# =============================================================================


class TestFetchMetadata:
    @pytest.mark.asyncio
    async def test_returns_empty(self) -> None:
        from application_sdk.handler import MetadataInput

        handler = OpenAPIHandler()
        result = await handler.fetch_metadata(MetadataInput())

        assert result.objects == []
