"""Unit tests for OpenAPIApiClient.

Tests API methods with respx-mocked httpx to verify correct spec fetching,
content-type detection, JSON/YAML parsing, and ZIP extraction.
No real network calls are made.
"""

from __future__ import annotations

import io
import orjson
import zipfile

import pytest
import respx
import httpx
from application_sdk.errors import InvalidInputError

from app.api_client import OpenAPIApiClient


# =============================================================================
# Helpers
# =============================================================================


PETSTORE_JSON = orjson.dumps(
    {
        "openapi": "3.0.4",
        "info": {"title": "Petstore", "version": "1.0.0"},
        "paths": {
            "/pets": {"get": {"summary": "List pets"}},
            "/pet/{petId}": {
                "get": {"summary": "Get pet"},
                "delete": {"summary": "Delete pet"},
            },
        },
    }
)

PETSTORE_YAML = b"""
openapi: "3.0.4"
info:
  title: PetstoreYAML
  version: "1.0.0"
paths:
  /pets:
    get:
      summary: List pets
"""


def _make_zip_bytes(files: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP archive with the given filenames and content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# =============================================================================
# TestFetchSpecJson
# =============================================================================


class TestFetchSpecJson:
    @pytest.mark.asyncio
    @respx.mock
    async def test_returns_list_with_one_dict(self) -> None:
        """fetch_spec should return a list containing one parsed dict."""
        respx.get("https://example.com/api.json").mock(
            return_value=httpx.Response(
                200, content=PETSTORE_JSON, headers={"content-type": "application/json"}
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/api.json")
        await client.close()

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["info"]["title"] == "Petstore"
        assert result[0]["openapi"] == "3.0.4"

    @pytest.mark.asyncio
    @respx.mock
    async def test_paths_are_present(self) -> None:
        respx.get("https://example.com/api.json").mock(
            return_value=httpx.Response(
                200, content=PETSTORE_JSON, headers={"content-type": "application/json"}
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/api.json")
        await client.close()

        assert "/pets" in result[0]["paths"]
        assert "/pet/{petId}" in result[0]["paths"]

    @pytest.mark.asyncio
    @respx.mock
    async def test_raises_on_http_error(self) -> None:
        """fetch_spec should propagate HTTP errors."""
        respx.get("https://example.com/missing.json").mock(
            return_value=httpx.Response(404, content=b"Not Found")
        )
        client = OpenAPIApiClient()
        with pytest.raises(httpx.HTTPStatusError):
            await client.fetch_spec("https://example.com/missing.json")
        await client.close()


# =============================================================================
# TestFetchSpecYaml
# =============================================================================


class TestFetchSpecYaml:
    @pytest.mark.asyncio
    @respx.mock
    async def test_yaml_content_type_parsed(self) -> None:
        """YAML content-type triggers YAML parsing."""
        respx.get("https://example.com/api").mock(
            return_value=httpx.Response(
                200, content=PETSTORE_YAML, headers={"content-type": "application/yaml"}
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/api")
        await client.close()

        assert result[0]["info"]["title"] == "PetstoreYAML"

    @pytest.mark.asyncio
    @respx.mock
    async def test_yaml_url_extension_triggers_yaml_parsing(self) -> None:
        """URL ending in .yaml triggers YAML parsing even with generic content-type."""
        respx.get("https://example.com/openapi.yaml").mock(
            return_value=httpx.Response(
                200,
                content=PETSTORE_YAML,
                headers={"content-type": "application/octet-stream"},
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/openapi.yaml")
        await client.close()

        assert result[0]["info"]["title"] == "PetstoreYAML"

    @pytest.mark.asyncio
    @respx.mock
    async def test_yml_extension_triggers_yaml_parsing(self) -> None:
        """.yml extension also triggers YAML parsing."""
        respx.get("https://example.com/openapi.yml").mock(
            return_value=httpx.Response(
                200, content=PETSTORE_YAML, headers={"content-type": "text/plain"}
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/openapi.yml")
        await client.close()

        assert result[0]["info"]["title"] == "PetstoreYAML"


# =============================================================================
# TestFetchSpecZip
# =============================================================================


class TestFetchSpecZip:
    @pytest.mark.asyncio
    @respx.mock
    async def test_zip_with_single_json_spec(self) -> None:
        """ZIP containing a single JSON spec returns one parsed dict."""
        zip_bytes = _make_zip_bytes({"openapi.json": PETSTORE_JSON})
        respx.get("https://example.com/bundle.zip").mock(
            return_value=httpx.Response(
                200, content=zip_bytes, headers={"content-type": "application/zip"}
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/bundle.zip")
        await client.close()

        assert len(result) == 1
        assert result[0]["info"]["title"] == "Petstore"

    @pytest.mark.asyncio
    @respx.mock
    async def test_zip_url_extension_triggers_zip_parsing(self) -> None:
        """URL ending in .zip triggers ZIP parsing."""
        zip_bytes = _make_zip_bytes({"api.json": PETSTORE_JSON})
        respx.get("https://example.com/bundle.zip").mock(
            return_value=httpx.Response(
                200,
                content=zip_bytes,
                headers={"content-type": "application/octet-stream"},
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/bundle.zip")
        await client.close()

        assert len(result) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_zip_skips_non_spec_files(self) -> None:
        """ZIP files that aren't .json/.yaml/.yml are skipped."""
        zip_bytes = _make_zip_bytes(
            {
                "openapi.json": PETSTORE_JSON,
                "README.txt": b"just a readme",
                "data.csv": b"col1,col2",
            }
        )
        respx.get("https://example.com/bundle.zip").mock(
            return_value=httpx.Response(
                200, content=zip_bytes, headers={"content-type": "application/zip"}
            )
        )
        client = OpenAPIApiClient()
        result = await client.fetch_spec("https://example.com/bundle.zip")
        await client.close()

        assert len(result) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_empty_zip_raises_value_error(self) -> None:
        """ZIP with no valid spec files raises ValueError."""
        zip_bytes = _make_zip_bytes({"README.txt": b"no specs here"})
        respx.get("https://example.com/bundle.zip").mock(
            return_value=httpx.Response(
                200, content=zip_bytes, headers={"content-type": "application/zip"}
            )
        )
        client = OpenAPIApiClient()
        with pytest.raises(InvalidInputError, match="No valid OpenAPI specs found"):
            await client.fetch_spec("https://example.com/bundle.zip")
        await client.close()


# =============================================================================
# TestAuthHeader
# =============================================================================


class TestAuthHeader:
    def test_auth_header_included_when_provided(self) -> None:
        """An auth_header is included in the HTTP headers."""
        client = OpenAPIApiClient(auth_header="Bearer test-token")
        headers = dict(client._client.headers)
        assert "authorization" in headers or "Authorization" in headers
        auth_value = headers.get("authorization") or headers.get("Authorization", "")
        assert "Bearer test-token" in auth_value

    def test_no_auth_header_when_empty(self) -> None:
        """Empty auth_header does not add Authorization header."""
        client = OpenAPIApiClient(auth_header="")
        headers = dict(client._client.headers)
        assert "authorization" not in headers and "Authorization" not in headers


# =============================================================================
# TestClose
# =============================================================================


class TestClose:
    @pytest.mark.asyncio
    async def test_close_is_awaitable(self) -> None:
        """close() should be awaitable and not raise."""
        client = OpenAPIApiClient()
        await client.close()  # Should not raise
