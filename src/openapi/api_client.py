"""OpenAPI HTTP client.

A stateless helper that fetches and parses an OpenAPI spec document from a URL.
Supports both JSON and YAML response formats.

This is NOT a framework component — it is a plain async utility used inside
the extract_spec @task method.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# Methods we surface as available_operations (uppercase)
_TRACKED_METHODS = {"get", "post", "put", "patch", "delete"}


class OpenAPIApiClient:
    """Stateless OpenAPI spec fetcher.

    Fetches a single OpenAPI spec document from a URL and parses it into
    a dict. Supports JSON and YAML content types.
    """

    def __init__(self, auth_header: str = "") -> None:
        """Create the client.

        Args:
            auth_header: Optional HTTP Authorization header value for private
                spec endpoints (e.g. 'Bearer my-token'). Empty for public specs.
        """
        headers: dict[str, str] = {"Accept": "application/json, application/yaml, text/yaml, */*"}
        if auth_header:
            headers["Authorization"] = auth_header

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=60.0,
            follow_redirects=True,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def fetch_spec(self, spec_url: str) -> list[dict]:
        """Fetch and parse an OpenAPI spec document from the given URL.

        Detects JSON, YAML, or ZIP from the Content-Type header or URL
        extension and parses accordingly. ZIP archives may contain multiple
        JSON/YAML spec files — each is returned as a separate dict.

        Args:
            spec_url: Full URL to the OpenAPI JSON, YAML, or ZIP document.

        Returns:
            List of parsed spec documents. Typically one item; multiple for ZIP.

        Raises:
            httpx.HTTPStatusError: If the HTTP response indicates an error.
            ValueError: If the response cannot be parsed.
        """
        logger.info("fetching OpenAPI spec", extra={"url": spec_url})
        response = await self._client.get(spec_url)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()

        # ZIP handling: extract all JSON/YAML specs from the archive
        is_zip = "zip" in content_type or spec_url.endswith(".zip")
        if is_zip:
            return self._parse_zip(response.content, spec_url)

        spec = self._parse_body(response.content, content_type, spec_url)
        path_count = len(spec.get("paths", {}))
        title = spec.get("info", {}).get("title", "<unknown>")
        openapi_version = spec.get("openapi", spec.get("swagger", "?"))
        logger.info(
            "spec fetched",
            extra={"title": title, "openapi": openapi_version, "paths": path_count},
        )
        return [spec]

    def _parse_body(self, content: bytes, content_type: str, url: str) -> dict:
        """Parse JSON or YAML bytes into a dict."""
        is_yaml = (
            "yaml" in content_type
            or url.endswith(".yaml")
            or url.endswith(".yml")
        )
        if is_yaml:
            import yaml

            return yaml.safe_load(content.decode("utf-8"))
        return httpx.Response(200, content=content).json()

    def _parse_zip(self, content: bytes, source_url: str) -> list[dict]:
        """Extract and parse all JSON/YAML spec files from a ZIP archive."""
        import io
        import zipfile

        specs: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if not (name.endswith(".json") or name.endswith(".yaml") or name.endswith(".yml")):
                    continue
                with zf.open(name) as f:
                    raw = f.read()
                content_type = "application/yaml" if (name.endswith(".yaml") or name.endswith(".yml")) else "application/json"
                try:
                    spec = self._parse_body(raw, content_type, name)
                    if isinstance(spec, dict) and "openapi" in spec or "swagger" in spec:
                        logger.info("extracted spec from ZIP", extra={"file": name})
                        specs.append(spec)
                except Exception as exc:
                    logger.warning("skipping file in ZIP", extra={"file": name, "error": str(exc)})
        if not specs:
            raise ValueError(f"No valid OpenAPI specs found in ZIP from {source_url}")
        return specs
