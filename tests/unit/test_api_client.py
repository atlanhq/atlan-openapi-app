"""Unit tests for OpenAPIApiClient.

Tests API methods with respx-mocked httpx to verify correct spec fetching,
content-type detection, JSON/YAML parsing, and ZIP extraction.
No real network calls are made.
"""

from __future__ import annotations

import io
import zipfile

import httpx
import orjson
import pytest
import respx
from application_sdk.errors import InvalidInputError

from app.api_client import OpenAPIApiClient
from app.errors import (
    SpecFetchAuthError,
    SpecFetchClientError,
    SpecFetchForbiddenError,
    SpecFetchRateLimitedError,
    SpecNotFoundError,
    SpecParseError,
    SpecSourceUnavailableError,
)

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
    async def test_raises_typed_on_http_error(self) -> None:
        """fetch_spec re-raises HTTP errors as typed AppErrors (CONNECT-812
        PF-20 class) — never a raw httpx.HTTPStatusError."""
        respx.get("https://example.com/missing.json").mock(
            return_value=httpx.Response(404, content=b"Not Found")
        )
        client = OpenAPIApiClient()
        with pytest.raises(SpecNotFoundError):
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
    async def test_zip_skips_unparseable_spec_file(self) -> None:
        """A .json member that fails to parse is skipped (logged), not fatal,
        as long as another member in the ZIP parses to a valid spec."""
        zip_bytes = _make_zip_bytes(
            {
                "openapi.json": PETSTORE_JSON,
                "corrupt.json": b"{not valid json!!!",
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
        assert result[0]["info"]["title"] == "Petstore"

    @pytest.mark.asyncio
    @respx.mock
    async def test_zip_rejects_json_file_without_openapi_or_swagger_key(self) -> None:
        """A well-formed JSON file that isn't an OpenAPI/Swagger document
        (no 'openapi' or 'swagger' key) must be excluded from the results,
        even though it parses successfully and has a .json extension."""
        zip_bytes = _make_zip_bytes(
            {
                "openapi.json": PETSTORE_JSON,
                "config.json": orjson.dumps({"unrelated": "config", "version": 2}),
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
        assert result[0]["info"]["title"] == "Petstore"

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


# =============================================================================
# TestFetchErrorClassification — CONNECT-812 PF-20/EP-02 class
# =============================================================================


class TestFetchErrorClassification:
    """Every failure on the fetch path must cross the activity boundary as a
    typed AppError with the right audience/retryable semantics — never a raw
    httpx or parse exception (CONNECT-812 PF-20 class)."""

    URL = "https://example.com/api.json"

    async def _fetch_expecting(self, exc_type: type[Exception]):
        client = OpenAPIApiClient()
        try:
            with pytest.raises(exc_type) as excinfo:
                await client.fetch_spec(self.URL)
        finally:
            await client.close()
        return excinfo.value

    @pytest.mark.asyncio
    @respx.mock
    async def test_401_raises_auth_error(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(401))
        err = await self._fetch_expecting(SpecFetchAuthError)
        assert "401" in err.message

    @pytest.mark.asyncio
    @respx.mock
    async def test_403_raises_forbidden_error(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(403))
        await self._fetch_expecting(SpecFetchForbiddenError)

    @pytest.mark.asyncio
    @respx.mock
    async def test_404_raises_not_found_error(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(404))
        err = await self._fetch_expecting(SpecNotFoundError)
        assert err.resource_identifier == self.URL

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_raises_rate_limited_error(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(429))
        await self._fetch_expecting(SpecFetchRateLimitedError)

    @pytest.mark.asyncio
    @respx.mock
    async def test_500_raises_source_unavailable_retryable(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(503))
        err = await self._fetch_expecting(SpecSourceUnavailableError)
        assert err.http_status == 503

    @pytest.mark.asyncio
    @respx.mock
    async def test_other_4xx_raises_client_error(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(418))
        err = await self._fetch_expecting(SpecFetchClientError)
        assert err.value_summary == "HTTP 418"

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_error_says_could_not_connect(self) -> None:
        respx.get(self.URL).mock(side_effect=httpx.ConnectError("boom"))
        err = await self._fetch_expecting(SpecSourceUnavailableError)
        assert "could not connect" in err.message
        assert err.network_error == "ConnectError"

    @pytest.mark.asyncio
    @respx.mock
    async def test_read_timeout_is_not_reported_as_connect_failure(self) -> None:
        """EP-02 (CONNECT-812): a read timeout means the endpoint WAS reached.
        The message must say 'timed out', and must not send the user to check
        network configuration that provably works."""
        respx.get(self.URL).mock(side_effect=httpx.ReadTimeout("slow"))
        err = await self._fetch_expecting(SpecSourceUnavailableError)
        assert "timed out" in err.message
        assert "could not connect" not in err.message
        assert err.network_error == "ReadTimeout"

    @pytest.mark.asyncio
    @respx.mock
    async def test_invalid_json_body_raises_parse_error(self) -> None:
        respx.get(self.URL).mock(
            return_value=httpx.Response(
                200,
                content=b"{not json",
                headers={"content-type": "application/json"},
            )
        )
        await self._fetch_expecting(SpecParseError)

    @pytest.mark.asyncio
    @respx.mock
    async def test_non_dict_yaml_scalar_raises_parse_error(self) -> None:
        respx.get("https://example.com/api.yaml").mock(
            return_value=httpx.Response(
                200,
                content=b"just a scalar string",
                headers={"content-type": "application/yaml"},
            )
        )
        client = OpenAPIApiClient()
        try:
            with pytest.raises(SpecParseError) as excinfo:
                await client.fetch_spec("https://example.com/api.yaml")
        finally:
            await client.close()
        assert "expected" in excinfo.value.message
