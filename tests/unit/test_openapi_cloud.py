"""Unit tests for OpenAPI cloud import mode wiring.

Tests the CLOUD path: api_client.fetch_spec handles local files produced by
download_cloud_spec, and the full extract pipeline works with a local file
path. No Temporal or cloud infrastructure required.

Download dispatch logic (_has_valid_auth) is tested in test_cloud_storage.py.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import orjson

_PETSTORE_SPEC = {
    "openapi": "3.0.4",
    "info": {"title": "Petstore (CLOUD test)", "version": "1.0.0"},
    "paths": {
        "/pets": {"get": {"summary": "List pets"}},
        "/pets/{petId}": {
            "get": {"summary": "Get pet"},
            "delete": {"summary": "Delete pet"},
        },
    },
}

_CLOUD_CONNECTION_QN = "default/api/test-openapi-cloud"


class TestOpenAPIConnectorCloudWiring:
    """CLOUD import mode wiring tests — no Temporal, no cloud services."""

    async def test_cloud_local_file_extraction(self, tmp_path: Path) -> None:
        """Verify fetch_spec handles a local file path (CLOUD download result)."""
        from app.api_client import OpenAPIApiClient

        spec_path = tmp_path / "petstore.json"
        spec_path.write_bytes(orjson.dumps(_PETSTORE_SPEC))

        client = OpenAPIApiClient()
        try:
            specs = await client.fetch_spec(str(spec_path))
        finally:
            await client.close()

        assert len(specs) == 1
        assert specs[0]["info"]["title"] == "Petstore (CLOUD test)"
        assert len(specs[0]["paths"]) == 2

    async def test_cloud_local_yaml_file(self, tmp_path: Path) -> None:
        """Verify fetch_spec handles a local YAML file."""
        from app.api_client import OpenAPIApiClient

        yaml_content = """
openapi: "3.0.4"
info:
  title: "YAML Cloud Test"
  version: "1.0.0"
paths:
  /health:
    get:
      summary: "Health check"
"""
        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(yaml_content)

        client = OpenAPIApiClient()
        try:
            specs = await client.fetch_spec(str(spec_path))
        finally:
            await client.close()

        assert len(specs) == 1
        assert specs[0]["info"]["title"] == "YAML Cloud Test"
        assert "/health" in specs[0]["paths"]

    async def test_cloud_local_zip_file(self, tmp_path: Path) -> None:
        """Verify fetch_spec handles a local ZIP file with multiple specs."""
        from app.api_client import OpenAPIApiClient

        spec1 = {
            "openapi": "3.0.0",
            "info": {"title": "Spec One", "version": "1.0"},
            "paths": {"/a": {"get": {"summary": "A"}}},
        }
        spec2 = {
            "openapi": "3.0.0",
            "info": {"title": "Spec Two", "version": "1.0"},
            "paths": {"/b": {"post": {"summary": "B"}}},
        }

        zip_path = tmp_path / "specs.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("spec1.json", orjson.dumps(spec1))
            zf.writestr("spec2.json", orjson.dumps(spec2))

        client = OpenAPIApiClient()
        try:
            specs = await client.fetch_spec(str(zip_path))
        finally:
            await client.close()

        assert len(specs) == 2
        titles = {s["info"]["title"] for s in specs}
        assert "Spec One" in titles
        assert "Spec Two" in titles

    async def test_cloud_extract_from_local_file(self, tmp_path: Path) -> None:
        """Verify the full extract pipeline works with a local file path."""
        from app.connector import _extract_spec_async
        from application_sdk.observability.logger_adaptor import get_logger

        spec_path = tmp_path / "petstore.json"
        spec_path.write_bytes(orjson.dumps(_PETSTORE_SPEC))

        spec_file, path_file, spec_count, path_count = await _extract_spec_async(
            spec_url=str(spec_path),
            connection_qualified_name=_CLOUD_CONNECTION_QN,
            auth_header="",
            logger=get_logger("test"),
        )

        assert spec_count == 1
        assert path_count == 2
        assert Path(spec_file.local_path).exists()
        assert Path(path_file.local_path).exists()
