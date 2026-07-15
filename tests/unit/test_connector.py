"""Unit tests for _extract_spec_async in connector.py.

Covers the three bug-fixes for BLDX-1363:
  1. paths: null is handled gracefully (no AttributeError)
  2. OAS 3.1 webhooks are extracted as APIPath records
  3. available_operations is sorted alphabetically
"""

from __future__ import annotations

from pathlib import Path

import orjson

from app.connector import _extract_spec_async
from application_sdk.observability.logger_adaptor import get_logger

CONN_QN = "default/api/test-conn"
_LOGGER = get_logger("test_connector")


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
