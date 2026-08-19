"""OpenAPI HTTP client.

A stateless helper that fetches and parses an OpenAPI spec document from a URL.
Supports both JSON and YAML response formats.

This is NOT a framework component — it is a plain async utility used inside
the extract_spec @task method.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

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
    SpecRedirectNotFollowedError,
    SpecSourceUnavailableError,
    SpecUrlInvalidError,
    ZipNoSpecFoundError,
)

logger = get_logger(__name__)

# Methods we surface as available_operations (uppercase)
_TRACKED_METHODS = {"get", "post", "put", "patch", "delete"}


def redact_url(url: str) -> str:
    """Return ``url`` with everything credential-bearing removed.

    A spec URL is routinely a pre-signed one — an Azure Blob SAS, an S3
    presigned GET, a GitHub token in a query parameter — so the query string is
    a secret, and so is any ``user:pass@`` userinfo. ``FailureDetails.message``
    and its evidence fields are **not** redacted on the wire: whatever goes in
    lands in Temporal history, the Automation Engine, and the connector-pulse
    ``check_matrix``. Only ``cause_repr`` is sanitized by the SDK.

    So every user-visible mention of a spec URL — messages, ``endpoint``,
    ``resource``, ``resource_identifier``, log lines — goes through here first,
    keeping enough to identify the endpoint (scheme, host, port, path) and
    nothing that authenticates to it.

    Non-URL inputs (the local file paths the CLOUD path produces) are returned
    unchanged; they carry no credential.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable url>"
    if not parts.scheme or not parts.netloc:
        return url
    try:
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
    except ValueError:
        # Malformed port — drop the whole authority rather than echo it back.
        netloc = "<invalid host>"
    redacted = urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    if parts.query:
        redacted = f"{redacted}?<redacted>"
    return redacted


class RedactedSpecFetchCause(Exception):
    """Stand-in for an httpx exception whose own message embeds the spec URL.

    ``AppError(cause=...)`` is the right place for diagnostic detail, and the
    SDK sanitizes it into ``cause_repr`` — but that sanitizer looks for
    credential-shaped *tokens*, not for a URL that is itself a credential.
    httpx renders the full request URL into every ``HTTPStatusError`` and most
    ``RequestError`` messages, so handing one straight to ``cause=`` puts a SAS
    signature into ``cause_repr`` even when the message beside it was redacted.

    This preserves the original exception's type name and detail with the URL
    swapped for its redacted form.
    """

    def __init__(self, original_type: str, detail: str) -> None:
        super().__init__(f"{original_type}: {detail}")
        self.original_type = original_type


def _redacted_cause(exc: Exception, spec_url: str) -> Exception:
    """Wrap ``exc`` so its detail survives but the spec URL's query does not."""
    detail = str(exc)
    if spec_url and spec_url in detail:
        detail = detail.replace(spec_url, redact_url(spec_url))
    query = urlsplit(spec_url).query if spec_url else ""
    if query and query in detail:
        detail = detail.replace(query, "<redacted>")
    return RedactedSpecFetchCause(type(exc).__name__, detail)


async def _resolve_host(hostname: str, port: int | None) -> list[str]:
    """Resolve ``hostname`` to addresses without blocking the event loop.

    A seam as much as a helper: it is the one place tests stub so the suite
    never touches a real resolver.
    """
    resolved = await asyncio.get_running_loop().getaddrinfo(
        hostname, port, type=socket.SOCK_STREAM
    )
    return [info[4][0] for info in resolved]


def _is_blocked_address(address: str) -> bool:
    """Whether a resolved address is off-limits for an outbound spec fetch."""
    ip = ipaddress.ip_address(address)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


async def validate_spec_url(spec_url: str) -> None:
    """Reject non-HTTPS and private or local-network spec endpoints.

    Async on purpose. Name resolution is network I/O, and this runs inside both
    a Temporal activity and the preflight gate's budgeted handler call. A
    synchronous ``socket.getaddrinfo`` blocks the event loop, which means it
    escapes the gate's cancellation entirely (cancellation lands at an
    ``await``) and stalls every other activity sharing the worker; an overrun
    then classifies as ``source_unverifiable``, which aborts the run once the
    app opts into hard mode. Resolving through the loop's threaded resolver
    keeps the call cancellable and the worker responsive.

    Policy note: this is an SSRF control, and it is deliberately strict —
    HTTPS-only, and the hostname must resolve exclusively to public addresses.
    That also rejects spec endpoints hosted on a private network. Loosening it
    (an allowlist, a per-connection opt-out) is a product decision, not a code
    cleanup.
    """
    parsed = urlsplit(spec_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SpecUrlInvalidError(
            message="spec_url must be an HTTPS URL with a hostname",
            field="spec_url",
            constraint="HTTPS URL with a public hostname",
            value_summary="invalid URL",
        )
    try:
        addresses = set(await _resolve_host(parsed.hostname, parsed.port))
    except (OSError, ValueError) as exc:
        raise SpecUrlInvalidError(
            message="spec_url hostname could not be resolved",
            field="spec_url",
            constraint="HTTPS URL with a resolvable public hostname",
            value_summary="unresolvable hostname",
            cause=exc,
        ) from exc
    if any(_is_blocked_address(address) for address in addresses):
        raise SpecUrlInvalidError(
            message="spec_url must not resolve to a private or local network",
            field="spec_url",
            constraint="hostname must resolve only to public IP addresses",
            value_summary="private or local address",
        )


def _classify_http_status(status: int, spec_url: str, exc: Exception) -> AppError:
    """Map an HTTP error status on the spec-fetch path to a typed AppError.

    CONNECT-812 PF-20/EP-02 class: an untyped exception crossing the activity
    boundary carries no FailureDetails, so the failure is unattributable and
    lands on the customer as a raw stack trace. Messages carry the status and
    exception type only — never the interpolated exception text, and never the
    raw URL (PF-18: ``message`` and the evidence fields are not redacted on the
    wire, so they get :func:`redact_url`; detail travels via ``cause``, which
    the SDK sanitizes into ``cause_repr``).
    """
    safe_url = redact_url(spec_url)
    safe_cause = _redacted_cause(exc, spec_url)
    if 300 <= status < 400:
        # Redirects are not followed (see OpenAPIApiClient.__init__), so a 3xx
        # is a terminal answer here rather than a hop. Without this branch it
        # fell through to the generic 4xx leaf and read as "endpoint must serve
        # the spec document to a plain GET", which is unactionable.
        return SpecRedirectNotFollowedError(
            message=f"spec endpoint returned HTTP {status} (redirect) for {safe_url}",
            field="spec_url",
            constraint="spec_url must serve the document directly, without a redirect",
            value_summary=f"HTTP {status}",
            suggested_action=(
                "Point spec_url at the document's final location. Redirects are "
                "not followed, because the redirect target is not covered by the "
                "URL safety check applied to spec_url."
            ),
            cause=safe_cause,
        )
    if status == 401:
        return SpecFetchAuthError(
            message=f"spec endpoint returned HTTP 401 (unauthorized) for {safe_url}",
            failure_reason="HTTP 401",
            suggested_action=(
                "Verify the spec URL is publicly accessible, or that any "
                "authorization the endpoint requires is configured. If the URL "
                "is pre-signed, check that the signature has not expired."
            ),
            cause=safe_cause,
        )
    if status == 403:
        return SpecFetchForbiddenError(
            message=f"spec endpoint returned HTTP 403 (forbidden) for {safe_url}",
            resource=safe_url,
            suggested_action=(
                "Verify the requesting principal is allowed to read the spec "
                "document at this URL. If the URL is pre-signed (S3 presigned, "
                "Azure SAS), check that the signature has not expired."
            ),
            cause=safe_cause,
        )
    if status == 404:
        return SpecNotFoundError(
            message=f"spec endpoint returned HTTP 404 (not found) for {safe_url}",
            resource_type="openapi_spec",
            resource_identifier=safe_url,
            suggested_action="Verify the spec URL points at an existing document.",
            cause=safe_cause,
        )
    if status == 429:
        return SpecFetchRateLimitedError(
            message=f"spec endpoint returned HTTP 429 (rate limited) for {safe_url}",
            suggested_action="Retry later, or reduce request frequency to the endpoint.",
            cause=safe_cause,
        )
    if status >= 500:
        return SpecSourceUnavailableError(
            message=f"spec endpoint returned HTTP {status} (server error) for {safe_url}",
            endpoint=safe_url,
            http_status=status,
            suggested_action="The spec server is erroring; retry once it is healthy.",
            cause=safe_cause,
        )
    return SpecFetchClientError(
        message=f"spec endpoint returned HTTP {status} for {safe_url}",
        field="spec_url",
        constraint="endpoint must serve the spec document to a plain GET",
        value_summary=f"HTTP {status}",
        cause=safe_cause,
    )


def _classify_request_error(exc: Exception, spec_url: str) -> AppError:
    """Map any httpx failure on the spec-fetch path to a typed AppError.

    The single classifier for both surfaces: ``fetch_spec`` (extraction) and
    ``probe_spec_url`` (preflight) route every httpx exception through here, so
    the check and the run can never disagree about what a given failure means.
    """
    safe_url = redact_url(spec_url)
    safe_cause = _redacted_cause(exc, spec_url)
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status(exc.response.status_code, spec_url, exc)
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return SpecSourceUnavailableError(
            message=(
                f"could not connect to spec endpoint {safe_url} ({type(exc).__name__})"
            ),
            endpoint=safe_url,
            network_error=type(exc).__name__,
            suggested_action=(
                "Verify the URL host is reachable from Atlan and that "
                "DNS/firewall rules allow the connection."
            ),
            cause=safe_cause,
        )
    if isinstance(exc, httpx.TimeoutException):
        # EP-02 (CONNECT-812): a read/pool timeout is NOT a connect failure —
        # the endpoint was reached, it just didn't answer in time. Say so, and
        # don't send the user to check network config that is provably working.
        return SpecSourceUnavailableError(
            message=(
                f"connected to spec endpoint {safe_url} but timed out "
                f"waiting for the response ({type(exc).__name__})"
            ),
            endpoint=safe_url,
            network_error=type(exc).__name__,
            suggested_action=(
                "The endpoint is reachable but slow to answer; retry, or "
                "check the spec server's load and the document's size."
            ),
            cause=safe_cause,
        )
    return SpecSourceUnavailableError(
        message=(f"network error fetching spec from {safe_url} ({type(exc).__name__})"),
        endpoint=safe_url,
        network_error=type(exc).__name__,
        cause=safe_cause,
    )


class OpenAPIApiClient:
    """Stateless OpenAPI spec fetcher.

    Fetches a single OpenAPI spec document from a URL and parses it into
    a dict. Supports JSON and YAML content types.
    """

    def __init__(self, auth_header: str = "", timeout: float = 60.0) -> None:
        """Create the client.

        Args:
            auth_header: Optional HTTP Authorization header value for private
                spec endpoints (e.g. 'Bearer my-token'). Empty for public specs.
            timeout: Per-request timeout in seconds. The preflight probe passes
                its slice of the gate budget here; extraction keeps the default.

        Redirects are deliberately **not** followed: a redirect target is not
        covered by :func:`validate_spec_url`, so following one would let a
        public hostname bounce the request onto a private address. A 3xx is
        surfaced as :class:`~app.errors.SpecRedirectNotFollowedError`.
        """
        headers: dict[str, str] = {
            "Accept": "application/json, application/yaml, text/yaml, */*"
        }
        if auth_header:
            headers["Authorization"] = auth_header

        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def probe_spec_url(self, spec_url: str) -> str:
        """Ask the spec endpoint the same question ``fetch_spec`` asks, cheaply.

        A streaming GET abandoned after the status line: the server sees the
        identical request the extraction path sends — same client, same
        ``Accept``, same redirect policy, same URL validation — so a 403 here is
        the same 403 the run would die on, attributed before the run instead of
        after it. The body is never downloaded, so a large spec cannot eat the
        preflight budget.

        Sharing the client with :meth:`fetch_spec` is the point: parity that
        depends on two call sites being kept in step is parity that drifts.

        Returns:
            The response's ``content-type`` header, lower-cased (``""`` if the
            endpoint sent none).

        Raises:
            AppError: The same typed errors :meth:`fetch_spec` raises.
        """
        await validate_spec_url(spec_url)
        try:
            async with self._client.stream("GET", spec_url) as response:
                response.raise_for_status()
                return response.headers.get("content-type", "").lower()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            raise _classify_request_error(exc, spec_url) from exc

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
            AppError: A typed leaf for every failure on this path — URL
                validation, HTTP status, network, and parse. Nothing untyped
                crosses the activity boundary (CONNECT-812 PF-20).
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
        logger.info("fetching OpenAPI spec url=%s", redact_url(spec_url))
        await validate_spec_url(spec_url)
        try:
            response = await self._client.get(spec_url)
            response.raise_for_status()
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            raise _classify_request_error(exc, spec_url) from exc

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
        safe_url = redact_url(url)
        try:
            if is_yaml:
                import yaml

                parsed = yaml.safe_load(content.decode("utf-8"))
            else:
                parsed = httpx.Response(200, content=content).json()
        except Exception as exc:
            raise SpecParseError(
                message=(
                    f"could not parse spec document from {safe_url} as {fmt} "
                    f"({type(exc).__name__})"
                ),
                field="spec_url",
                constraint="document must be valid OpenAPI JSON or YAML",
                cause=exc,
            ) from exc
        if not isinstance(parsed, dict):
            raise SpecParseError(
                message=(
                    f"spec document from {safe_url} parsed to "
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
                message=(
                    f"No valid OpenAPI specs found in ZIP from {redact_url(source_url)}"
                ),
                field="spec_url",
                constraint="ZIP must contain at least one valid OpenAPI JSON/YAML spec",
            )
        logger.info(
            "extracted specs from ZIP count=%d source=%s",
            len(specs),
            redact_url(source_url),
        )
        return specs
