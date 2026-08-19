"""Unit tests for _extract_spec_async in connector.py.

Covers the three bug-fixes for BLDX-1363:
  1. paths: null is handled gracefully (no AttributeError)
  2. OAS 3.1 webhooks are extracted as APIPath records
  3. available_operations is sorted alphabetically
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import orjson
import pytest
from application_sdk.contracts.storage import UploadOutput
from application_sdk.contracts.types import ConnectionRef, FileReference
from application_sdk.credentials.ref import CredentialRef
from application_sdk.errors import InternalError
from application_sdk.observability.logger_adaptor import get_logger

from app.connector import (
    OpenAPIConnector,
    _enc_hook,
    _extract_spec_async,
    _has_valid_auth,
    _iter_jsonl,
    _transform_blocking,
)
from app.contracts import (
    DownloadCloudSpecInput,
    DownloadCloudSpecOutput,
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    TransformInput,
    TransformOutput,
)
from app.errors import (
    CloudSpecLocationRequiredError,
    CloudSpecNotFoundError,
    ConnectionRequiredError,
    NoValidSpecsError,
    ObjectStoreCredentialError,
    SpecUrlRequiredError,
    TenantObjectStoreUnavailableError,
    UnknownImportTypeError,
)

CONN_QN = "default/api/test-conn"
_LOGGER = get_logger("test_connector")


def _make_connector() -> OpenAPIConnector:
    """Build an OpenAPIConnector with a stubbed AppContext.

    ``@task`` returns the raw function (it only attaches metadata), so task
    bodies can be invoked directly on an instance with a fake ``_context``.
    """
    connector = OpenAPIConnector()
    connector._context = SimpleNamespace(  # type: ignore[attr-defined]
        logger=_LOGGER,
        run_id="test-run",
        app_name="openapi",
        storage=None,
    )
    return connector


def _make_connector_for_run() -> OpenAPIConnector:
    """Build an OpenAPIConnector whose stubbed context also carries
    ``started_at`` (needed by ``run()``'s ``workflow_run_at_ms`` computation)."""
    connector = OpenAPIConnector()
    connector._context = SimpleNamespace(  # type: ignore[attr-defined]
        logger=_LOGGER,
        run_id="test-run",
        app_name="openapi",
        storage=None,
        started_at=datetime.now(timezone.utc),
    )
    return connector


def _conn_ref(qn: str = CONN_QN) -> ConnectionRef:
    return ConnectionRef.model_validate(
        {"typeName": "Connection", "attributes": {"qualifiedName": qn, "name": "t"}}
    )


def _base_input(**overrides: object) -> OpenAPIConnectorInput:
    kwargs: dict[str, object] = dict(
        connection=_conn_ref(),
        connection_usage="CREATE",
        import_type="URL",
        spec_url="https://example.com/openapi.json",
        load_to_atlan=False,
    )
    kwargs.update(overrides)
    return OpenAPIConnectorInput(**kwargs)


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
        from app.api_types import OpenAPIPathRecord
        from app.connector import _iter_jsonl

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
# extract_spec — fetches spec_url with no auth (the private-URL Bearer
# credential is a dropped feature; extract_spec no longer takes a credential).
# ---------------------------------------------------------------------------


class TestExtractSpecNoAuth:
    async def test_fetches_spec_url_without_credential(self, tmp_path: Path) -> None:
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "NoAuth", "version": "1.0"},
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

    async def test_empty_spec_url_raises(self) -> None:
        connector = _make_connector()
        with pytest.raises(SpecUrlRequiredError):
            await connector.extract_spec(
                ExtractSpecInput(spec_url="", connection_qualified_name=CONN_QN)
            )


# ---------------------------------------------------------------------------
# _iter_jsonl edge cases
# ---------------------------------------------------------------------------


class TestIterJsonl:
    def test_none_ref_yields_nothing(self) -> None:
        from app.api_types import OpenAPISpecRecord

        assert list(_iter_jsonl(None, OpenAPISpecRecord)) == []

    def test_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        from app.api_types import OpenAPISpecRecord

        ref = FileReference(local_path=str(tmp_path / "does-not-exist.jsonl"))
        assert list(_iter_jsonl(ref, OpenAPISpecRecord)) == []

    def test_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        from app.api_types import OpenAPISpecRecord

        p = tmp_path / "empty.jsonl"
        p.write_bytes(b"")
        ref = FileReference(local_path=str(p))
        assert list(_iter_jsonl(ref, OpenAPISpecRecord)) == []


class TestEncHook:
    def test_returns_str_of_object(self) -> None:
        class Weird:
            def __str__(self) -> str:
                return "weird-repr"

        assert _enc_hook(Weird()) == "weird-repr"


# ---------------------------------------------------------------------------
# _extract_spec_async — additional robustness branches
# ---------------------------------------------------------------------------


class TestExtractSpecMissingTitle:
    async def test_all_specs_skipped_raises_typed_error(self, tmp_path: Path) -> None:
        """CONNECT-812 EP-03: when every fetched document is skipped (missing
        info.title), extraction raises a typed error instead of returning zero
        records and letting the run finish green with nothing extracted."""
        spec = {
            "openapi": "3.0.4",
            "info": {"version": "1.0"},
            "paths": {"/a": {"get": {}}},
        }
        url = _write_spec(tmp_path, spec)
        with pytest.raises(NoValidSpecsError):
            await _run(url, tmp_path)

    async def test_partial_skip_keeps_valid_sibling(self, tmp_path: Path) -> None:
        """A title-less spec inside a ZIP is skipped, but a valid sibling still
        extracts — the EP-03 guard fires only when nothing at all is usable."""
        import zipfile

        good = {
            "openapi": "3.0.4",
            "info": {"title": "Good", "version": "1.0"},
            "paths": {"/a": {"get": {}}},
        }
        bad = {
            "openapi": "3.0.4",
            "info": {"version": "1.0"},
            "paths": {"/b": {"get": {}}},
        }
        zip_path = tmp_path / "specs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("good.json", orjson.dumps(good))
            zf.writestr("bad.json", orjson.dumps(bad))
        _, _, spec_count, path_count = await _run(str(zip_path), tmp_path)
        assert spec_count == 1
        assert path_count == 1


class TestExtractSpecMalformedPathItem:
    async def test_non_dict_path_item_is_skipped(self, tmp_path: Path) -> None:
        """A paths entry whose value isn't a dict (malformed spec) is skipped
        without raising, and valid siblings are still processed."""
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "Malformed", "version": "1.0"},
            "paths": {
                "/bad": "not-a-dict",
                "/good": {"get": {"summary": "ok"}},
            },
        }
        url = _write_spec(tmp_path, spec)
        _, _, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 1


class TestExtractSpecDescriptions:
    async def test_path_and_operation_descriptions_included(
        self, tmp_path: Path
    ) -> None:
        """Path-item level description and per-operation descriptions are both
        folded into the markdown description."""
        from app.api_types import OpenAPIPathRecord
        from app.connector import _iter_jsonl

        spec = {
            "openapi": "3.0.4",
            "info": {"title": "DescAPI", "version": "1.0"},
            "paths": {
                "/widgets": {
                    "description": "Widget collection endpoint.",
                    "get": {
                        "summary": "List widgets",
                        "description": "Returns all widgets.",
                    },
                }
            },
        }
        url = _write_spec(tmp_path, spec)
        _, path_file, _, path_count = await _run(url, tmp_path)
        assert path_count == 1

        records = list(_iter_jsonl(path_file, OpenAPIPathRecord))
        description = records[0].description
        assert "Widget collection endpoint." in description
        assert "**GET**" in description
        assert "Returns all widgets." in description


# ---------------------------------------------------------------------------
# _transform_blocking
# ---------------------------------------------------------------------------


class TestTransformBlocking:
    async def _extracted_files(self, tmp_path: Path):
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "TransformAPI", "version": "1.0"},
            "paths": {"/a": {"get": {"summary": "A"}}, "/b": {"post": {}}},
        }
        url = _write_spec(tmp_path, spec)
        spec_file, path_file, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 2
        return spec_file, path_file

    async def test_missing_connection_raises(self, tmp_path: Path) -> None:
        spec_file, path_file = await self._extracted_files(tmp_path)
        with pytest.raises(ConnectionRequiredError):
            _transform_blocking(
                TransformInput(
                    api_spec_file=spec_file,
                    api_path_file=path_file,
                    connection=None,
                    connection_qualified_name=CONN_QN,
                    emit_connection=True,
                    workflow_id="wf-1",
                    workflow_type="openapi",
                    workflow_run_at_ms=0,
                ),
                _LOGGER,
            )

    async def test_create_emits_connection_and_all_records(
        self, tmp_path: Path
    ) -> None:
        spec_file, path_file = await self._extracted_files(tmp_path)
        result = _transform_blocking(
            TransformInput(
                api_spec_file=spec_file,
                api_path_file=path_file,
                connection=_conn_ref(),
                connection_qualified_name=CONN_QN,
                emit_connection=True,
                workflow_id="wf-1",
                workflow_type="openapi",
                workflow_run_at_ms=1234,
            ),
            _LOGGER,
        )
        assert result.api_spec_count == 1
        assert result.api_path_count == 2
        assert result.output_file is not None
        lines = Path(result.output_file.local_path).read_bytes().splitlines()
        # 1 connection + 1 spec + 2 paths = 4 NDJSON rows
        assert len(lines) == 4

    async def test_reuse_does_not_emit_connection(self, tmp_path: Path) -> None:
        spec_file, path_file = await self._extracted_files(tmp_path)
        result = _transform_blocking(
            TransformInput(
                api_spec_file=spec_file,
                api_path_file=path_file,
                connection=_conn_ref(),
                connection_qualified_name=CONN_QN,
                emit_connection=False,
                workflow_id="wf-1",
                workflow_type="openapi",
                workflow_run_at_ms=1234,
            ),
            _LOGGER,
        )
        lines = Path(result.output_file.local_path).read_bytes().splitlines()
        # No connection row: 1 spec + 2 paths = 3 NDJSON rows
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# download_cloud_spec task
# ---------------------------------------------------------------------------


class TestDownloadCloudSpec:
    def _connector_with_context(
        self, storage: object, resolve_credential_raw: AsyncMock
    ) -> OpenAPIConnector:
        connector = OpenAPIConnector()
        connector._context = SimpleNamespace(  # type: ignore[attr-defined]
            logger=_LOGGER,
            run_id="test-run",
            app_name="openapi",
            storage=storage,
            resolve_credential_raw=resolve_credential_raw,
        )
        return connector

    async def test_valid_key_auth_uses_external_store_with_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_instance = SimpleNamespace(
            download=AsyncMock(return_value=["/tmp/x/openapi.json"])
        )
        fake_cls = SimpleNamespace(
            from_credentials=lambda creds: fake_instance,
        )
        monkeypatch.setattr("application_sdk.storage.cloud.CloudStore", fake_cls)

        connector = self._connector_with_context(
            storage=None,
            resolve_credential_raw=AsyncMock(
                return_value={
                    "authType": "s3",
                    "username": "AKIA",
                    "password": "secret",
                    "extra": {},
                }
            ),
        )
        out = await connector.download_cloud_spec(
            DownloadCloudSpecInput(
                openapi_credential=CredentialRef(credential_guid="g1"),
                spec_prefix="specs",
                spec_key="openapi.json",
            )
        )
        assert out.spec_files[0].local_path == "/tmp/x/openapi.json"
        fake_instance.download.assert_awaited_once()
        assert fake_instance.download.call_args.kwargs["key"] == "specs/openapi.json"

    async def test_rejected_credential_raises_typed_with_severed_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CONNECT-812 PF-17: a credential CloudStore rejects surfaces as a
        typed error with the exception chain severed (``from None``) so
        loguru's ``diagnose`` traceback can never annotate the frame holding
        the plaintext credential dict. The cause survives only as a redacted
        summary."""

        def _boom(creds: dict) -> None:
            raise ValueError("bad creds: password=super-secret-value")

        fake_cls = SimpleNamespace(from_credentials=_boom)
        monkeypatch.setattr("application_sdk.storage.cloud.CloudStore", fake_cls)

        connector = self._connector_with_context(
            storage=None,
            resolve_credential_raw=AsyncMock(
                return_value={
                    "authType": "s3",
                    "username": "AKIA",
                    "password": "super-secret-value",
                    "extra": {},
                }
            ),
        )
        with pytest.raises(ObjectStoreCredentialError) as excinfo:
            await connector.download_cloud_spec(
                DownloadCloudSpecInput(
                    openapi_credential=CredentialRef(credential_guid="g1"),
                    spec_prefix="specs",
                )
            )
        assert excinfo.value.__cause__ is None
        assert excinfo.value.__suppress_context__ is True
        assert "super-secret-value" not in str(excinfo.value)

    async def test_no_valid_auth_and_no_tenant_store_raises(self) -> None:
        connector = self._connector_with_context(
            storage=None,
            resolve_credential_raw=AsyncMock(
                return_value={"username": "", "password": "", "extra": {}}
            ),
        )
        with pytest.raises(TenantObjectStoreUnavailableError):
            await connector.download_cloud_spec(
                DownloadCloudSpecInput(
                    openapi_credential=CredentialRef(credential_guid="g1"),
                    spec_prefix="specs",
                )
            )

    async def test_no_valid_auth_falls_back_to_tenant_store_with_prefix(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_instance = SimpleNamespace(
            download=AsyncMock(return_value=["/tmp/y/a.json", "/tmp/y/b.yaml"])
        )

        def _ctor(*args: object, **kwargs: object) -> SimpleNamespace:
            return fake_instance

        monkeypatch.setattr("application_sdk.storage.cloud.CloudStore", _ctor)

        connector = self._connector_with_context(
            storage=object(),
            resolve_credential_raw=AsyncMock(
                return_value={"username": "", "password": "", "extra": {}}
            ),
        )
        out = await connector.download_cloud_spec(
            DownloadCloudSpecInput(
                openapi_credential=CredentialRef(credential_guid="g1"),
                spec_prefix="specs",
                spec_key="",
            )
        )
        assert len(out.spec_files) == 2
        fake_instance.download.assert_awaited_once()
        call_kwargs = fake_instance.download.call_args.kwargs
        assert call_kwargs["prefix"] == "specs"
        assert ".json" in call_kwargs["suffix_filter"]

    async def test_legacy_cloud_source_guid_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_instance = SimpleNamespace(
            download=AsyncMock(return_value=["/tmp/z/spec.json"])
        )
        fake_cls = SimpleNamespace(from_credentials=lambda creds: fake_instance)
        monkeypatch.setattr("application_sdk.storage.cloud.CloudStore", fake_cls)

        resolve_mock = AsyncMock(
            return_value={
                "authType": "gcs",
                "username": "u",
                "password": "p",
                "extra": {},
            }
        )
        connector = self._connector_with_context(
            storage=None, resolve_credential_raw=resolve_mock
        )
        out = await connector.download_cloud_spec(
            DownloadCloudSpecInput(
                openapi_credential=None,
                cloud_source="legacy-guid-123",
                spec_key="spec.json",
            )
        )
        assert out.spec_files[0].local_path == "/tmp/z/spec.json"
        resolved_ref = resolve_mock.call_args.args[0]
        assert resolved_ref.credential_guid == "legacy-guid-123"

    async def test_no_credential_at_all_uses_tenant_store(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_instance = SimpleNamespace(
            download=AsyncMock(return_value=["/tmp/w/spec.json"])
        )

        def _ctor(*args: object, **kwargs: object) -> SimpleNamespace:
            return fake_instance

        monkeypatch.setattr("application_sdk.storage.cloud.CloudStore", _ctor)

        connector = self._connector_with_context(
            storage=object(), resolve_credential_raw=AsyncMock()
        )
        out = await connector.download_cloud_spec(
            DownloadCloudSpecInput(
                openapi_credential=None,
                cloud_source="",
                spec_prefix="specs",
            )
        )
        assert out.spec_files[0].local_path == "/tmp/w/spec.json"


# ---------------------------------------------------------------------------
# run() orchestration
# ---------------------------------------------------------------------------


class TestRunValidationErrors:
    async def test_reuse_missing_connection_qn_raises(self) -> None:
        connector = _make_connector_for_run()
        input = _base_input(connection_usage="REUSE", connection_qualified_name="")
        with pytest.raises(ConnectionRequiredError):
            await connector.run(input)

    async def test_reuse_placeholder_qn_raises(self) -> None:
        connector = _make_connector_for_run()
        input = _base_input(
            connection_usage="REUSE",
            connection_qualified_name="{{connection_qualified_name}}",
        )
        with pytest.raises(ConnectionRequiredError):
            await connector.run(input)

    async def test_create_missing_connection_qn_raises(self) -> None:
        connector = _make_connector_for_run()
        input = _base_input(connection=ConnectionRef(), connection_usage="CREATE")
        with pytest.raises(ConnectionRequiredError):
            await connector.run(input)

    async def test_cloud_missing_spec_location_raises(self) -> None:
        connector = _make_connector_for_run()
        input = _base_input(import_type="CLOUD", spec_prefix="", spec_key="")
        with pytest.raises(CloudSpecLocationRequiredError):
            await connector.run(input)

    async def test_url_missing_spec_url_raises(self) -> None:
        connector = _make_connector_for_run()
        input = _base_input(import_type="URL", spec_url="")
        with pytest.raises(SpecUrlRequiredError):
            await connector.run(input)

    async def test_unknown_import_type_raises(self) -> None:
        connector = _make_connector_for_run()
        input = _base_input(import_type="FTP")
        with pytest.raises(UnknownImportTypeError):
            await connector.run(input)


class TestRunUrlCreateHappyPath:
    async def test_full_pipeline_create_url_with_upload(self) -> None:
        connector = _make_connector_for_run()

        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(
                api_spec_file=FileReference(local_path="/tmp/spec.jsonl"),
                api_path_file=FileReference(local_path="/tmp/path.jsonl"),
                api_spec_count=1,
                api_path_count=3,
            )
        )
        connector.transform = AsyncMock(  # type: ignore[method-assign]
            return_value=TransformOutput(
                output_file=FileReference(local_path="/tmp/out.json"),
                api_spec_count=1,
                api_path_count=3,
            )
        )
        connector.upload = AsyncMock(  # type: ignore[method-assign]
            return_value=UploadOutput(
                ref=FileReference(
                    local_path="/tmp/out.json",
                    storage_path="raw/openapi/transformed/out.json",
                )
            )
        )

        input = _base_input(
            connection_usage="CREATE", import_type="URL", load_to_atlan=True
        )
        result = await connector.run(input)

        connector.extract_spec.assert_awaited_once()
        connector.transform.assert_awaited_once()
        connector.upload.assert_awaited_once()
        assert result.api_spec_count == 1
        assert result.api_path_count == 3
        assert result.total_scanned == 4
        assert result.publish_completed is True
        assert result.assertion_only_enabled is False
        assert result.transformed_data_prefix == "raw/openapi/transformed"


class TestRunReusePath:
    async def test_reuse_skips_upload_when_load_to_atlan_false(self) -> None:
        connector = _make_connector_for_run()
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(
                api_spec_file=FileReference(local_path="/tmp/spec.jsonl"),
                api_path_file=FileReference(local_path="/tmp/path.jsonl"),
                api_spec_count=1,
                api_path_count=2,
            )
        )
        connector.transform = AsyncMock(  # type: ignore[method-assign]
            return_value=TransformOutput(
                output_file=FileReference(local_path="/tmp/out.json"),
                api_spec_count=1,
                api_path_count=2,
            )
        )
        connector.upload = AsyncMock()  # type: ignore[method-assign]

        input = _base_input(
            connection_usage="REUSE",
            connection_qualified_name=CONN_QN,
            import_type="URL",
            load_to_atlan=False,
        )
        result = await connector.run(input)

        connector.upload.assert_not_awaited()
        assert result.assertion_only_enabled is True
        assert result.publish_completed is False
        assert result.connection_qualified_name == CONN_QN


class TestRunCloudPath:
    async def test_cloud_import_with_explicit_credential_multiple_spec_files(
        self,
    ) -> None:
        connector = _make_connector_for_run()
        connector.download_cloud_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=DownloadCloudSpecOutput(
                spec_files=[
                    FileReference(local_path="/tmp/a.json"),
                    FileReference(local_path="/tmp/b.json"),
                ]
            )
        )
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(
                api_spec_file=FileReference(local_path="/tmp/spec.jsonl"),
                api_path_file=FileReference(local_path="/tmp/path.jsonl"),
                api_spec_count=1,
                api_path_count=1,
            )
        )
        connector.transform = AsyncMock(  # type: ignore[method-assign]
            return_value=TransformOutput(
                output_file=FileReference(local_path="/tmp/out.json"),
                api_spec_count=2,
                api_path_count=2,
            )
        )

        cred_ref = CredentialRef(name="openapi_source", credential_type="openapi")
        input = _base_input(
            import_type="CLOUD",
            spec_prefix="specs",
            openapi_credential=cred_ref,
            load_to_atlan=False,
        )
        result = await connector.run(input)

        connector.download_cloud_spec.assert_awaited_once()
        call_input = connector.download_cloud_spec.call_args.args[0]
        assert call_input.openapi_credential == cred_ref
        assert connector.extract_spec.await_count == 2
        assert result.total_scanned == 4


class TestRunCloudNoFiles:
    async def test_zero_downloaded_files_raises_typed_error(self) -> None:
        """CONNECT-812 EP-03: a CLOUD import whose prefix/key matches nothing
        raises instead of finishing green with zero assets."""
        connector = _make_connector_for_run()
        connector.download_cloud_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=DownloadCloudSpecOutput(spec_files=[])
        )
        input = _base_input(
            import_type="CLOUD",
            spec_prefix="specs",
            load_to_atlan=False,
        )
        with pytest.raises(CloudSpecNotFoundError):
            await connector.run(input)


class TestHasValidAuth:
    """CONNECT-812 PF-17: _has_valid_auth must never raise — its frame holds
    the plaintext credential dict, and a traceback through it would be
    diagnose-annotated into the logs."""

    def test_malformed_extra_json_reads_as_no_role_auth(self) -> None:
        assert (
            _has_valid_auth({"username": "", "password": "", "extra": "{not json"})
            is False
        )

    def test_non_dict_extra_reads_as_no_role_auth(self) -> None:
        assert _has_valid_auth({"username": "", "password": "", "extra": "42"}) is False

    def test_key_auth_still_detected_with_malformed_extra(self) -> None:
        assert (
            _has_valid_auth({"username": "u", "password": "p", "extra": "{not json"})
            is True
        )

    def test_role_auth_detected(self) -> None:
        assert (
            _has_valid_auth(
                {"username": "", "password": "", "extra": {"aws_role_arn": "arn:x"}}
            )
            is True
        )


class TestRunCloudCredentialResolution:
    async def test_no_routable_credential_falls_back_to_none(self) -> None:
        connector = _make_connector_for_run()
        connector.download_cloud_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=DownloadCloudSpecOutput(
                spec_files=[FileReference(local_path="/tmp/a.json")]
            )
        )
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(api_spec_count=0, api_path_count=0)
        )

        input = _base_input(
            import_type="CLOUD",
            spec_prefix="specs",
            openapi_credential=None,
            load_to_atlan=False,
        )
        await connector.run(input)

        call_input = connector.download_cloud_spec.call_args.args[0]
        assert call_input.openapi_credential is None

    async def test_credential_guid_resolves_to_ref(self) -> None:
        connector = _make_connector_for_run()
        connector.download_cloud_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=DownloadCloudSpecOutput(
                spec_files=[FileReference(local_path="/tmp/a.json")]
            )
        )
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(api_spec_count=0, api_path_count=0)
        )

        input = _base_input(
            import_type="CLOUD",
            spec_prefix="specs",
            openapi_credential=None,
            credential_guid="guid-abc",
            load_to_atlan=False,
        )
        await connector.run(input)

        call_input = connector.download_cloud_spec.call_args.args[0]
        assert call_input.openapi_credential is not None
        assert call_input.openapi_credential.credential_guid == "guid-abc"


class TestRunZeroScanned:
    async def test_zero_scanned_skips_transform_and_upload(self) -> None:
        connector = _make_connector_for_run()
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(api_spec_count=0, api_path_count=0)
        )
        connector.transform = AsyncMock()  # type: ignore[method-assign]
        connector.upload = AsyncMock()  # type: ignore[method-assign]

        input = _base_input(load_to_atlan=True)
        result = await connector.run(input)

        connector.transform.assert_not_awaited()
        connector.upload.assert_not_awaited()
        assert result.total_scanned == 0
        assert result.publish_completed is False
        assert result.output_file is None


class TestRunPartialZeroAmongMultiple:
    async def test_zero_count_extract_result_skipped_in_transform_loop(self) -> None:
        connector = _make_connector_for_run()
        connector.download_cloud_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=DownloadCloudSpecOutput(
                spec_files=[
                    FileReference(local_path="/tmp/a.json"),
                    FileReference(local_path="/tmp/b.json"),
                ]
            )
        )
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                ExtractSpecOutput(
                    api_spec_file=FileReference(local_path="/tmp/spec1.jsonl"),
                    api_path_file=FileReference(local_path="/tmp/path1.jsonl"),
                    api_spec_count=1,
                    api_path_count=1,
                ),
                ExtractSpecOutput(api_spec_count=0, api_path_count=0),
            ]
        )
        connector.transform = AsyncMock(  # type: ignore[method-assign]
            return_value=TransformOutput(
                output_file=FileReference(local_path="/tmp/out.json"),
                api_spec_count=1,
                api_path_count=1,
            )
        )

        input = _base_input(
            import_type="CLOUD", spec_prefix="specs", load_to_atlan=False
        )
        result = await connector.run(input)

        connector.transform.assert_awaited_once()
        assert result.total_scanned == 2


class TestRunUploadStoragePathMissing:
    async def test_missing_storage_path_raises_internal_error(self) -> None:
        connector = _make_connector_for_run()
        connector.extract_spec = AsyncMock(  # type: ignore[method-assign]
            return_value=ExtractSpecOutput(
                api_spec_file=FileReference(local_path="/tmp/spec.jsonl"),
                api_path_file=FileReference(local_path="/tmp/path.jsonl"),
                api_spec_count=1,
                api_path_count=0,
            )
        )
        connector.transform = AsyncMock(  # type: ignore[method-assign]
            return_value=TransformOutput(
                output_file=FileReference(local_path="/tmp/out.json"),
                api_spec_count=1,
                api_path_count=0,
            )
        )
        connector.upload = AsyncMock(  # type: ignore[method-assign]
            return_value=UploadOutput(
                ref=FileReference(local_path="/tmp/out.json", storage_path=None)
            )
        )

        input = _base_input(load_to_atlan=True)
        with pytest.raises(InternalError):
            await connector.run(input)


class TestTransformTaskWrapper:
    async def test_transform_task_delegates_to_transform_blocking(
        self, tmp_path: Path
    ) -> None:
        """The @task wrapper delegates to ``_transform_blocking`` via
        ``run_in_thread``; stub ``run_in_thread`` to call the function inline
        so the wrapper's own logging/plumbing lines are exercised."""
        spec = {
            "openapi": "3.0.4",
            "info": {"title": "WrapperAPI", "version": "1.0"},
            "paths": {"/a": {"get": {}}},
        }
        url = _write_spec(tmp_path, spec)
        spec_file, path_file, spec_count, path_count = await _run(url, tmp_path)
        assert spec_count == 1
        assert path_count == 1

        connector = _make_connector()
        connector.run_in_thread = AsyncMock(  # type: ignore[method-assign]
            side_effect=lambda func, *a, **kw: func(*a, **kw)
        )
        result = await connector.transform(
            TransformInput(
                api_spec_file=spec_file,
                api_path_file=path_file,
                connection=_conn_ref(),
                connection_qualified_name=CONN_QN,
                emit_connection=True,
                workflow_id="wf-1",
                workflow_type="openapi",
                workflow_run_at_ms=0,
            )
        )
        assert result.api_spec_count == 1
        assert result.api_path_count == 1
        connector.run_in_thread.assert_awaited_once()
