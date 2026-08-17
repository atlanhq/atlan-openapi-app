"""OpenAPI HTTP client.

A stateless helper that fetches and parses an OpenAPI spec document from a URL.
Supports both JSON and YAML response formats.

This is NOT a framework component — it is a plain async utility used inside
the extract_spec @task method.
"""

from __future__ import annotations

import httpx
from application_sdk.errors.base import AppError
from application_sdk.observability.logger_adaptor import get_logger

from app.errors import (
    SpecFetchAuthError,
    SpecFetchClientError,
    SpecFetchForbiddenError,
    SpecFetchRateLimitedError,
    SpecNotFoundError,
    SpecParseError,
    SpecSourceUnavailableError,
    ZipNoSpecFoundError,
)

logger = get_logger(__name__)

# Methods we surface as available_operations (uppercase)
_TRACKED_METHODS = {"get", "post", "put", "patch", "delete"}


def _classify_http_status(status: int, spec_url: str, exc: Exception) -> AppError:
    """Map an HTTP error status on the spec-fetch path to a typed AppError.

    CONNECT-812 PF-20/EP-02 class: an untyped exception crossing the activity
    boundary carries no FailureDetails, so the failure is unattributable and
    lands on the customer as a raw stack trace. Messages carry the status and
    exception type only — never the interpolated exception text (PF-18: the
    ``message`` field is not redacted on the wire; detail travels via ``cause``,
    which is sanitized into ``cause_repr``).
    """
    if status == 401:
        return SpecFetchAuthError(
            message=f"spec endpoint returned HTTP 401 (unauthorized) for {spec_url}",
            failure_reason="HTTP 401",
            suggested_action=(
                "Verify the spec URL is publicly accessible, or that any "
                "authorization the endpoint requires is configured."
            ),
            cause=exc,
        )
    if status == 403:
        return SpecFetchForbiddenError(
            message=f"spec endpoint returned HTTP 403 (forbidden) for {spec_url}",
            resource=spec_url,
            suggested_action=(
                "Verify the requesting principal is allowed to read the spec "
                "document at this URL."
            ),
            cause=exc,
        )
    if status == 404:
        return SpecNotFoundError(
            message=f"spec endpoint returned HTTP 404 (not found) for {spec_url}",
            resource_type="openapi_spec",
            resource_identifier=spec_url,
            suggested_action="Verify the spec URL points at an existing document.",
            cause=exc,
        )
    if status == 429:
        return SpecFetchRateLimitedError(
            message=f"spec endpoint returned HTTP 429 (rate limited) for {spec_url}",
            suggested_action="Retry later, or reduce request frequency to the endpoint.",
            cause=exc,
        )
    if status >= 500:
        return SpecSourceUnavailableError(
            message=f"spec endpoint returned HTTP {status} (server error) for {spec_url}",
            endpoint=spec_url,
            http_status=status,
            suggested_action="The spec server is erroring; retry once it is healthy.",
            cause=exc,
        )
    return SpecFetchClientError(
        message=f"spec endpoint returned HTTP {status} for {spec_url}",
        field="spec_url",
        constraint="endpoint must serve the spec document to a plain GET",
        value_summary=f"HTTP {status}",
        cause=exc,
    )


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
        headers: dict[str, str] = {
            "Accept": "application/json, application/yaml, text/yaml, */*"
        }
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
        """Fetch and parse an OpenAPI spec document from a URL or local file path.

        Detects JSON, YAML, or ZIP from the Content-Type header (URL) or file
        extension (local path) and parses accordingly. ZIP archives may contain
        multiple JSON/YAML spec files — each is returned as a separate dict.

        Args:
            spec_url: Full URL to the OpenAPI JSON, YAML, or ZIP document,
                OR a local file path (for CLOUD import mode where the file
                has already been downloaded).

        Returns:
            List of parsed spec documents. Typically one item; multiple for ZIP.

        Raises:
            httpx.HTTPStatusError: If the HTTP response indicates an error.
            ValueError: If the response cannot be parsed.
        """
        # Local file path (from CLOUD download) — read directly
        import os

        if "://" not in spec_url and os.path.isfile(spec_url):
            logger.info("reading OpenAPI spec from local file=%s", spec_url)
            with open(spec_url, "rb") as f:
                content = f.read()
            content_type = ""
            is_zip = spec_url.endswith(".zip")
            if is_zip:
                return self._parse_zip(content, spec_url)
            spec = self._parse_body(content, content_type, spec_url)
            path_count = len(spec.get("paths") or {})
            title = spec.get("info", {}).get("title", "<unknown>")
            openapi_version = spec.get("openapi", spec.get("swagger", "?"))
            logger.info(
                "spec read from file title=%s openapi=%s paths=%d",
                title,
                openapi_version,
                path_count,
            )
            return [spec]

        # HTTP URL — fetch remotely. Every network failure is re-raised typed
        # (CONNECT-812 PF-20 class): a raw httpx exception crossing the
        # activity boundary has no FailureDetails and is unattributable.
        logger.info("fetching OpenAPI spec url=%s", spec_url)
        try:
            response = await self._client.get(spec_url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _classify_http_status(
                exc.response.status_code, spec_url, exc
            ) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SpecSourceUnavailableError(
                message=(
                    f"could not connect to spec endpoint {spec_url} "
                    f"({type(exc).__name__})"
                ),
                endpoint=spec_url,
                network_error=type(exc).__name__,
                suggested_action=(
                    "Verify the URL host is reachable from Atlan and that "
                    "DNS/firewall rules allow the connection."
                ),
                cause=exc,
            ) from exc
        except httpx.TimeoutException as exc:
            # EP-02 (CONNECT-812): a read/pool timeout is NOT a connect
            # failure — the endpoint was reached, it just didn't answer in
            # time. Say so, and don't send the user to check network config
            # that is provably working.
            raise SpecSourceUnavailableError(
                message=(
                    f"connected to spec endpoint {spec_url} but timed out "
                    f"waiting for the response ({type(exc).__name__})"
                ),
                endpoint=spec_url,
                network_error=type(exc).__name__,
                suggested_action=(
                    "The endpoint is reachable but slow to answer; retry, or "
                    "check the spec server's load and the document's size."
                ),
                cause=exc,
            ) from exc
        except httpx.RequestError as exc:
            raise SpecSourceUnavailableError(
                message=(
                    f"network error fetching spec from {spec_url} "
                    f"({type(exc).__name__})"
                ),
                endpoint=spec_url,
                network_error=type(exc).__name__,
                cause=exc,
            ) from exc

        content_type = response.headers.get("content-type", "").lower()

        # ZIP handling: extract all JSON/YAML specs from the archive
        is_zip = "zip" in content_type or spec_url.endswith(".zip")
        if is_zip:
            return self._parse_zip(response.content, spec_url)

        spec = self._parse_body(response.content, content_type, spec_url)
        path_count = len(spec.get("paths") or {})
        title = spec.get("info", {}).get("title", "<unknown>")
        openapi_version = spec.get("openapi", spec.get("swagger", "?"))
        logger.info(
            "spec fetched title=%s openapi=%s paths=%d",
            title,
            openapi_version,
            path_count,
        )
        return [spec]

    def _parse_body(self, content: bytes, content_type: str, url: str) -> dict:
        """Parse JSON or YAML bytes into a dict.

        Raises:
            SpecParseError: If the bytes cannot be parsed, or parse to
                something other than a mapping (e.g. a YAML scalar) — a
                non-dict here surfaces later as an opaque ``AttributeError``
                deep in extraction otherwise.
        """
        is_yaml = (
            "yaml" in content_type or url.endswith(".yaml") or url.endswith(".yml")
        )
        fmt = "YAML" if is_yaml else "JSON"
        try:
            if is_yaml:
                import yaml

                parsed = yaml.safe_load(content.decode("utf-8"))
            else:
                parsed = httpx.Response(200, content=content).json()
        except Exception as exc:
            raise SpecParseError(
                message=(
                    f"could not parse spec document from {url} as {fmt} "
                    f"({type(exc).__name__})"
                ),
                field="spec_url",
                constraint="document must be valid OpenAPI JSON or YAML",
                cause=exc,
            ) from exc
        if not isinstance(parsed, dict):
            raise SpecParseError(
                message=(
                    f"spec document from {url} parsed to "
                    f"{type(parsed).__name__}, expected a {fmt} object"
                ),
                field="spec_url",
                constraint="document must be a single OpenAPI object",
                value_summary=type(parsed).__name__,
            )
        return parsed

    def _parse_zip(self, content: bytes, source_url: str) -> list[dict]:
        """Extract and parse all JSON/YAML spec files from a ZIP archive."""
        import io
        import zipfile

        specs: list[dict] = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if not (
                    name.endswith(".json")
                    or name.endswith(".yaml")
                    or name.endswith(".yml")
                ):
                    continue
                with zf.open(name) as f:
                    raw = f.read()
                content_type = (
                    "application/yaml"
                    if (name.endswith(".yaml") or name.endswith(".yml"))
                    else "application/json"
                )
                try:
                    spec = self._parse_body(raw, content_type, name)
                    if isinstance(spec, dict) and (
                        "openapi" in spec or "swagger" in spec
                    ):
                        logger.debug("extracted spec from ZIP file=%s", name)
                        specs.append(spec)
                except Exception:
                    logger.warning("skipping file in ZIP file=%s", name, exc_info=True)
        if not specs:
            raise ZipNoSpecFoundError(
                message=f"No valid OpenAPI specs found in ZIP from {source_url}",
                field="spec_url",
                constraint="ZIP must contain at least one valid OpenAPI JSON/YAML spec",
            )
        logger.info(
            "extracted specs from ZIP count=%d source=%s", len(specs), source_url
        )
        return specs
