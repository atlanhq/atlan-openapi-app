"""Unit tests for _extract_spec_async in connector.py.

Covers the three bug-fixes for BLDX-1363:
  1. paths: null is handled gracefully (no AttributeError)
  2. OAS 3.1 webhooks are extracted as APIPath records
  3. available_operations is sorted alphabetically
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import orjson
import pytest

from application_sdk.contracts.types import ConnectionRef, FileReference
from application_sdk.credentials.ref import CredentialRef
from application_sdk.observability.logger_adaptor import get_logger

from app.connector import OpenAPIConnector, _extract_spec_async
from app.contracts import (
    DownloadCloudSpecInput,
    DownloadCloudSpecOutput,
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    ResolveSourceTypeInput,
    ResolveSourceTypeOutput,
)
from app.errors import (
    SourceCredentialRequiredError,
    SpecUrlRequiredError,
    UnknownImportTypeError,
)

CONN_QN = "default/api/test-conn"
_LOGGER = get_logger("test_connector")


# ---------------------------------------------------------------------------
# helpers for context-driven task/run unit tests
# ---------------------------------------------------------------------------


def _make_connector(
    *,
    resolve_raw: dict[str, Any] | None = None,
    storage: Any = None,
) -> OpenAPIConnector:
    """Build an OpenAPIConnector with a stubbed AppContext.

    ``@task`` returns the raw function (only attaches metadata), so task-method
    bodies can be invoked directly on an instance with a fake ``_context``.
    """
    connector = OpenAPIConnector()
    resolve_mock = (
        AsyncMock(return_value=resolve_raw)
        if resolve_raw is not None
        else AsyncMock(return_value={})
    )
    connector._context = SimpleNamespace(  # type: ignore[attr-defined]
        logger=_LOGGER,
        run_id="test-run",
        app_name="openapi",
        storage=storage,
        resolve_credential_raw=resolve_mock,
    )
    return connector


def _connection_ref(qn: str = CONN_QN) -> ConnectionRef:
    return ConnectionRef.model_validate(
        {"typeName": "Connection", "attributes": {"qualifiedName": qn, "name": "c"}}
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_spec(tmp_path: Path, spec: dict, name: str = "spec.json") -> str:
    p = tmp_path / name
    p.write_bytes(orjson.dumps(spec))
    return str(p)


async def _run(spec_url: str, tmp_path: Path):
    return await _extract_spec_async(
        spec_url=spec_url,
        connection_qualified_name=CONN_QN,
        auth_header="",
        logger=_LOGGER,
    )


# ---------------------------------------------------------------------------
# paths: null should not raise AttributeError
# ---------------------------------------------------------------------------


class TestPathsNull:
    async def test_paths_null_produces_zero_api_paths(self, tmp_path: Path) -> None:
        """A spec with paths: null must not crash — just yield 0 APIPath records."""
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "NullPaths", "version": "1.0"},
            "paths": None,
        }
        url = _write_spec(tmp_path, spec)
        _, _, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 0

    async def test_paths_absent_produces_zero_api_paths(self, tmp_path: Path) -> None:
        """A spec with no 'paths' key must yield 0 APIPath records without error."""
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "NoPaths", "version": "1.0"},
        }
        url = _write_spec(tmp_path, spec)
        _, _, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 0


# ---------------------------------------------------------------------------
# OAS 3.1 webhooks extraction
# ---------------------------------------------------------------------------


class TestWebhooks:
    async def test_webhooks_produce_api_path_records(self, tmp_path: Path) -> None:
        """OAS 3.1 webhooks entries must be extracted as APIPath records."""
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "WebhookAPI", "version": "1.0"},
            "webhooks": {
                "/on-new-pet": {"post": {"summary": "New pet event"}},
                "/on-delete-pet": {"delete": {"summary": "Pet deleted"}},
            },
        }
        url = _write_spec(tmp_path, spec)
        _, _, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 2

    async def test_paths_and_webhooks_merged(self, tmp_path: Path) -> None:
        """Specs with both paths and webhooks produce APIPath records for all entries."""
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "HybridAPI", "version": "1.0"},
            "paths": {
                "/users": {"get": {"summary": "List users"}},
            },
            "webhooks": {
                "/on-user-created": {"post": {"summary": "User created event"}},
            },
        }
        url = _write_spec(tmp_path, spec)
        _, _, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 2

    async def test_webhooks_null_does_not_crash(self, tmp_path: Path) -> None:
        """A spec with webhooks: null must not crash."""
        spec = {
            "openapi": "3.1.0",
            "info": {"title": "NullWebhooks", "version": "1.0"},
            "paths": {"/pets": {"get": {"summary": "List pets"}}},
            "webhooks": None,
        }
        url = _write_spec(tmp_path, spec)
        _, _, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 1


# ---------------------------------------------------------------------------
# available_operations is sorted
# ---------------------------------------------------------------------------


class TestAvailableOperationsSorted:
    async def test_operations_are_alphabetically_sorted(self, tmp_path: Path) -> None:
        """available_operations must be sorted per SPEC.md hashable content."""
        from app.connector import _iter_jsonl
        from app.api_types import OpenAPIPathRecord

        spec = {
            "openapi": "3.0.4",
            "info": {"title": "SortedOps", "version": "1.0"},
            "paths": {
                "/resource": {
                    "post": {"summary": "Create"},
                    "get": {"summary": "Read"},
                    "delete": {"summary": "Delete"},
                }
            },
        }
        url = _write_spec(tmp_path, spec)
        _, path_file, _, path_count = await _run(url, tmp_path)
        assert path_count == 1

        records = list(_iter_jsonl(path_file, OpenAPIPathRecord))
        assert len(records) == 1
        ops = records[0].available_operations
        assert ops == sorted(ops), f"operations not sorted: {ops}"
        assert set(ops) == {"GET", "POST", "DELETE"}


class TestUnsubstitutedPlaceholder:
    """REUSE must reject an unresolved manifest placeholder before it leaks into
    object-store paths (CONNECT-55)."""

    def test_detects_mustache_placeholder(self) -> None:
        from app.connector import _is_unsubstituted_placeholder

        assert _is_unsubstituted_placeholder("{{connection_qualified_name}}")
        assert _is_unsubstituted_placeholder("default/api/{{epoch}}")

    def test_real_qualified_name_is_not_a_placeholder(self) -> None:
        from app.connector import _is_unsubstituted_placeholder

        assert not _is_unsubstituted_placeholder("default/api/1783959234")
        assert not _is_unsubstituted_placeholder("")


# ---------------------------------------------------------------------------
# resolve_source_type — returns only the non-secret authType selector
# ---------------------------------------------------------------------------


class TestResolveSourceType:
    async def test_returns_authtype(self) -> None:
        connector = _make_connector(
            resolve_raw={"authType": "s3", "username": "u", "password": "p"}
        )
        out = await connector.resolve_source_type(
            ResolveSourceTypeInput(openapi_credential=CredentialRef(name="src"))
        )
        assert out.auth_type == "s3"

    async def test_falls_back_to_auth_type_key(self) -> None:
        connector = _make_connector(resolve_raw={"auth_type": "url"})
        out = await connector.resolve_source_type(
            ResolveSourceTypeInput(openapi_credential=CredentialRef(name="src"))
        )
        assert out.auth_type == "url"

    async def test_none_credential_raises(self) -> None:
        connector = _make_connector()
        with pytest.raises(SourceCredentialRequiredError):
            await connector.resolve_source_type(
                ResolveSourceTypeInput(openapi_credential=None)
            )


# ---------------------------------------------------------------------------
# extract_spec — dual mode (credential → spec_url/auth_header, else local path)
# ---------------------------------------------------------------------------


class TestExtractSpecDualMode:
    async def test_credential_mode_reads_spec_url_and_auth_header(
        self, tmp_path: Path
    ) -> None:
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "CredMode", "version": "1.0"},
            "paths": {"/pets": {"get": {"summary": "List"}}},
        }
        local = tmp_path / "spec.json"
        local.write_bytes(orjson.dumps(spec))
        connector = _make_connector(
            resolve_raw={
                "authType": "url",
                "spec_url": str(local),
                "auth_header": "Bearer tok",
            }
        )
        out = await connector.extract_spec(
            ExtractSpecInput(
                openapi_credential=CredentialRef(name="src"),
                connection_qualified_name=CONN_QN,
            )
        )
        assert out.api_spec_count == 1
        assert out.api_path_count == 1
        # The credential was resolved to obtain spec_url (no top-level spec_url).
        connector.context.resolve_credential_raw.assert_awaited_once()

    async def test_local_path_mode_uses_input_spec_url(self, tmp_path: Path) -> None:
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "LocalMode", "version": "1.0"},
            "paths": {"/a": {"get": {}}, "/b": {"post": {}}},
        }
        local = tmp_path / "spec.json"
        local.write_bytes(orjson.dumps(spec))
        connector = _make_connector()
        out = await connector.extract_spec(
            ExtractSpecInput(spec_url=str(local), connection_qualified_name=CONN_QN)
        )
        assert out.api_spec_count == 1
        assert out.api_path_count == 2
        # No credential in local-path mode.
        connector.context.resolve_credential_raw.assert_not_awaited()

    async def test_empty_effective_spec_url_raises(self) -> None:
        connector = _make_connector()
        with pytest.raises(SpecUrlRequiredError):
            await connector.extract_spec(
                ExtractSpecInput(spec_url="", connection_qualified_name=CONN_QN)
            )


# ---------------------------------------------------------------------------
# download_cloud_spec — object key/prefix read from credential.extra
# ---------------------------------------------------------------------------


class TestDownloadCloudSpec:
    async def test_reads_key_and_prefix_from_extra(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import application_sdk.storage.cloud as cloud_mod

        downloaded = str(tmp_path / "openapi.json")
        fake_store = SimpleNamespace(download=AsyncMock(return_value=[downloaded]))
        monkeypatch.setattr(
            cloud_mod.CloudStore,
            "from_credentials",
            staticmethod(lambda data: fake_store),
        )
        connector = _make_connector(
            resolve_raw={
                "authType": "s3",
                "username": "u",
                "password": "p",
                "extra": {
                    "s3_bucket": "b",
                    "spec_prefix": "specs",
                    "spec_key": "openapi.json",
                },
            }
        )
        out = await connector.download_cloud_spec(
            DownloadCloudSpecInput(
                openapi_credential=CredentialRef(credential_guid="g")
            )
        )
        assert [r.local_path for r in out.spec_files] == [downloaded]
        # prefix + key assembled from extra.
        fake_store.download.assert_awaited_once()
        assert fake_store.download.call_args.kwargs["key"] == "specs/openapi.json"

    async def test_extra_as_json_string_is_parsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import application_sdk.storage.cloud as cloud_mod

        downloaded = str(tmp_path / "spec.json")
        fake_store = SimpleNamespace(download=AsyncMock(return_value=[downloaded]))
        monkeypatch.setattr(
            cloud_mod.CloudStore,
            "from_credentials",
            staticmethod(lambda data: fake_store),
        )
        connector = _make_connector(
            resolve_raw={
                "authType": "s3",
                "username": "u",
                "password": "p",
                "extra": orjson.dumps(
                    {"s3_bucket": "b", "spec_key": "only.json"}
                ).decode(),
            }
        )
        out = await connector.download_cloud_spec(
            DownloadCloudSpecInput(
                openapi_credential=CredentialRef(credential_guid="g")
            )
        )
        assert len(out.spec_files) == 1
        # No prefix → key is the bare object key.
        assert fake_store.download.call_args.kwargs["key"] == "only.json"

    async def test_none_credential_raises(self) -> None:
        connector = _make_connector()
        with pytest.raises(SourceCredentialRequiredError):
            await connector.download_cloud_spec(
                DownloadCloudSpecInput(openapi_credential=None)
            )


# ---------------------------------------------------------------------------
# run() dispatch — url vs object-store based on authType
# ---------------------------------------------------------------------------


class TestRunDispatch:
    def _make_run_connector(self, auth_type: str) -> OpenAPIConnector:
        connector = _make_connector()
        connector.resolve_source_type = AsyncMock(  # type: ignore[method-assign]
            return_value=ResolveSourceTypeOutput(auth_type=auth_type)
        )
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(api_spec_count=0, api_path_count=0)
        )
        connector.download_cloud_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=DownloadCloudSpecOutput(
                spec_files=[FileReference(local_path="/tmp/cloud/spec.json")]
            )
        )
        return connector

    def _input(self) -> OpenAPIConnectorInput:
        return OpenAPIConnectorInput(
            connection_usage="CREATE",
            connection=_connection_ref(),
            openapi_credential=CredentialRef(name="src"),
            load_to_atlan=False,
        )

    async def test_url_authtype_dispatches_to_extract_spec(self) -> None:
        connector = self._make_run_connector("url")
        await connector.run(self._input())
        connector.extract_spec.assert_awaited_once()
        connector.download_cloud_spec.assert_not_awaited()
        # URL mode forwards the credential to extract_spec (which resolves it).
        call_input = connector.extract_spec.call_args.args[0]
        assert call_input.openapi_credential is not None

    async def test_object_store_authtype_downloads_then_extracts(self) -> None:
        connector = self._make_run_connector("s3")
        await connector.run(self._input())
        connector.download_cloud_spec.assert_awaited_once()
        # One extract per downloaded local file, in local-path mode (no credential).
        connector.extract_spec.assert_awaited_once()
        call_input = connector.extract_spec.call_args.args[0]
        assert call_input.openapi_credential is None
        assert call_input.spec_url == "/tmp/cloud/spec.json"

    async def test_unknown_authtype_raises(self) -> None:
        connector = self._make_run_connector("ftp")
        with pytest.raises(UnknownImportTypeError):
            await connector.run(self._input())
