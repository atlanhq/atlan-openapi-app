"""HTTP handler for the OpenAPI Spec Loader connector.

Backs the boundary endpoints consumed by the Atlan UI and the SDK's injected
preflight gate:
  - POST /workflows/v1/auth     -> test_auth
  - POST /workflows/v1/check    -> preflight_check
  - POST /workflows/v1/metadata -> fetch_metadata

Closes the SDK preflight gap tracked in FND-264: this app had no ``Handler``
at all, so every run fell back to ``DefaultHandler`` (always READY, no
checks run). A stale spec_url credential (e.g. an expired Azure Blob SAS
signature) or an unreachable spec host could then only be discovered
mid-extraction, after ``extract_spec`` had already started. See
connector-pulse's ``sdk_preflight_gate_fleet_service.PREFLIGHT_OWNED_CATEGORIES``:
a run that PROCEEDS (no check exists, so the gate reports READY) and then
FAILS with a source-auth/source-connectivity error is classified
``missing_check`` — a real gap in the app, not a downstream issue.

Mirrors the ``import_type`` branch in ``OpenAPIConnector.run`` (app/connector.py):
URL mode probes ``spec_url`` directly (the same request ``extract_spec`` will
make, just cheaper); CLOUD mode validates the resolved object-store
credential the same way ``download_cloud_spec`` does (``_has_valid_auth``),
with a lightweight existence/connectivity check in place of a full download.

Does not touch the actual root cause of any given failure (e.g. rotating a
stale tenant credential) — this handler only gives the gate a real verdict to
catch it with, before extraction starts.
"""

from __future__ import annotations

from time import perf_counter
from typing import Any

import httpx

from app.api_client import OpenAPIApiClient
from application_sdk.errors import AuthError, InvalidInputError, SourceUnavailableError
from application_sdk.handler import (
    ApiMetadataOutput,
    AuthInput,
    AuthOutput,
    AuthStatus,
    Handler,
    HandlerCredential,
    MetadataInput,
    MetadataOutput,
    PreflightCheck,
    PreflightInput,
    PreflightOutput,
    PreflightStatus,
)
from application_sdk.observability.logger_adaptor import get_logger

logger = get_logger(__name__)

# HTTP status codes that mean "the credential/token presented to spec_url was
# rejected" — source-auth, not source-connectivity.
_AUTH_STATUS_CODES = frozenset({401, 403})


def _credentials_to_dict(creds: list[HandlerCredential]) -> dict[str, Any]:
    """Flatten ``HandlerCredential`` k/v pairs into the raw dict shape
    ``_has_valid_auth`` and ``CloudStore.from_credentials`` expect (top-level
    ``authType`` / ``username`` / ``password`` + nested ``extra``).

    Mirrors the S3/ADLS handlers' own flattening helper — the SDK's
    ``HandlerCredential.list_from_raw`` hoists nested ``extra`` keys to
    ``extra.<k>`` on the way out (see ``flatten_credentials_to_pairs``); this
    undoes that on the way in.
    """
    out: dict[str, Any] = {"extra": {}}
    for pair in creds:
        key = pair.key
        value = pair.value
        if key in ("auth-type", "authType"):
            out["authType"] = value
        elif key == "username":
            out["username"] = value
        elif key == "password":
            out["password"] = value
        elif key.startswith("extra."):
            out.setdefault("extra", {})[key[len("extra.") :]] = value
        else:
            out[key] = value
    return out


async def _check_url_spec(spec_url: str) -> PreflightCheck:
    """Cheap reachability probe for a URL-mode ``spec_url``.

    Mirrors the request ``_extract_spec_async`` will eventually make, via
    ``check_reachable`` (HEAD, falling back to a 1-byte ranged GET) rather
    than downloading the full spec document.
    """
    client = OpenAPIApiClient()
    try:
        status_code = await client.check_reachable(spec_url)
    except httpx.TransportError as exc:
        return PreflightCheck(
            name="specUrlReachable",
            passed=False,
            error=SourceUnavailableError(
                message=f"Could not reach {spec_url}: {exc}",
                endpoint=spec_url,
                network_error=str(exc),
                suggested_action=(
                    "Verify spec_url is correct and reachable from the "
                    "connector's network (DNS, firewall, TLS)."
                ),
                cause=exc,
            ).to_failure_details(),
        )
    finally:
        await client.close()

    if status_code in _AUTH_STATUS_CODES:
        return PreflightCheck(
            name="specUrlReachable",
            passed=False,
            error=AuthError(
                message=f"Could not authenticate to {spec_url}: HTTP {status_code}.",
                failure_reason=f"http_{status_code}",
                suggested_action=(
                    "The credential embedded in spec_url (e.g. a signed URL or "
                    "SAS token) is missing, expired, or invalid. Refresh it and "
                    "re-run."
                ),
            ).to_failure_details(),
        )
    if status_code >= 400:
        return PreflightCheck(
            name="specUrlReachable",
            passed=False,
            error=SourceUnavailableError(
                message=f"spec_url returned HTTP {status_code}: {spec_url}",
                endpoint=spec_url,
                http_status=status_code,
                suggested_action="Verify spec_url points at a live OpenAPI spec document.",
            ).to_failure_details(),
        )
    return PreflightCheck(
        name="specUrlReachable",
        passed=True,
        message=f"spec_url reachable (HTTP {status_code}).",
    )


async def _check_cloud_credential(
    credentials: list[HandlerCredential], spec_prefix: str
) -> PreflightCheck:
    """Validate the resolved object-store credential for CLOUD import mode.

    Mirrors ``download_cloud_spec``'s own dispatch: an app-supplied external
    credential must carry key- or role-based auth (``_has_valid_auth``). With
    no credential resolved at all, ``download_cloud_spec`` falls back to the
    tenant's own Dapr-configured object store — a path this handler has no
    way to probe from the HTTP/gate frame, so it is reported READY here
    (advisory: absence of an external credential is not itself a failure).
    """
    if not credentials:
        return PreflightCheck(
            name="cloudCredentialValid",
            passed=True,
            message=(
                "No external object-store credential resolved; falling back "
                "to the tenant object store (not probed by preflight)."
            ),
        )

    # Deferred: app.connector imports this module to register OpenAPIHandler
    # for SDK convention-based discovery, so a top-level import back here
    # would be circular.
    from app.connector import _has_valid_auth

    creds_dict = _credentials_to_dict(credentials)
    if not _has_valid_auth(creds_dict):
        return PreflightCheck(
            name="cloudCredentialValid",
            passed=False,
            error=AuthError(
                message=(
                    "The resolved object-store credential has neither "
                    "key-based (username/password) nor role-based "
                    "(extra.aws_role_arn) auth."
                ),
                suggested_action=(
                    "Provide a valid object-store credential (access "
                    "key/secret, or an assumable role ARN) for "
                    "import_type='CLOUD'."
                ),
            ).to_failure_details(),
        )

    from application_sdk.storage.cloud import CloudStore
    from application_sdk.storage.errors import StorageConfigError, StorageError

    try:
        store = CloudStore.from_credentials(creds_dict)
        prefix = spec_prefix.strip("/") if spec_prefix else ""
        await store.list(prefix=prefix)
    except StorageConfigError as exc:
        return PreflightCheck(
            name="cloudCredentialValid",
            passed=False,
            error=AuthError(
                message=f"Object-store credential is misconfigured: {exc}",
                suggested_action=(
                    "Verify authType and the matching key/role fields on the "
                    "object-store credential."
                ),
                cause=exc,
            ).to_failure_details(),
        )
    except StorageError as exc:
        return PreflightCheck(
            name="cloudCredentialValid",
            passed=False,
            error=SourceUnavailableError(
                message=f"Could not reach the object store: {exc}",
                suggested_action=(
                    "Verify the object-store bucket/container, spec_prefix, "
                    "and network access, then re-run."
                ),
                cause=exc,
            ).to_failure_details(),
        )

    return PreflightCheck(
        name="cloudCredentialValid",
        passed=True,
        message="Object-store credential authenticated and reachable.",
    )


def _blocked(checks: list[PreflightCheck], started: float) -> PreflightOutput:
    """Build a NOT_READY output from the checks accumulated so far."""
    failed = next((c for c in checks if not c.passed), None)
    return PreflightOutput(
        status=PreflightStatus.NOT_READY,
        checks=checks,
        message=failed.resolved_message if failed else "Preflight check failed.",
        total_duration_ms=(perf_counter() - started) * 1000,
    )


class OpenAPIHandler(Handler):
    """Handler backing the OpenAPI connector's auth/preflight/metadata endpoints."""

    async def test_auth(self, input: AuthInput) -> AuthOutput:
        """Test the credential this connection form carries, if any.

        URL-mode ``spec_url`` has no standalone credential — its only "auth"
        is whatever is embedded in the URL itself, which ``preflight_check``
        already probes. CLOUD mode's credential is the object-store
        credential, which is what this delegates to when present. With no
        credentials on the request at all, there is nothing to test:
        pass through as successful rather than blocking a form that has no
        credential-test surface to fail.
        """
        if not input.credentials:
            return AuthOutput(
                status=AuthStatus.SUCCESS,
                message=(
                    "No credential to test for this connection "
                    "(import_type='URL' has no standalone credential)."
                ),
            )

        from app.connector import _has_valid_auth

        creds_dict = _credentials_to_dict(list(input.credentials))
        if not _has_valid_auth(creds_dict):
            return AuthOutput(
                status=AuthStatus.INVALID_CREDENTIALS,
                message=(
                    "Object-store credential has neither key-based nor role-based auth."
                ),
            )

        from application_sdk.storage.cloud import CloudStore
        from application_sdk.storage.errors import StorageConfigError, StorageError

        try:
            store = CloudStore.from_credentials(creds_dict)
            await store.list()
        except StorageConfigError as exc:
            return AuthOutput(
                status=AuthStatus.INVALID_CREDENTIALS,
                message=f"Object-store credential is misconfigured: {exc}",
            )
        except StorageError as exc:
            return AuthOutput(
                status=AuthStatus.FAILED,
                message=f"Could not reach the object store: {exc}",
            )
        return AuthOutput(
            status=AuthStatus.SUCCESS,
            message="Object-store credential authenticated and reachable.",
        )

    async def preflight_check(self, input: PreflightInput) -> PreflightOutput:
        """Verify the connection is ready to extract, branching on import_type
        exactly as ``OpenAPIConnector.run`` does.

        Single implementation for both surfaces (the Test-Connection button
        and the injected pre-extraction gate) — ``PreflightInput.connection_config``
        carries the same ``import_type`` / ``spec_url`` / ``spec_prefix`` /
        ``spec_key`` fields the workflow input declares (``app/contracts.py``'s
        ``OpenAPIConnectorInput``).
        """
        started = perf_counter()
        config = input.connection_config
        import_type = config.get("import_type", "URL") or "URL"

        if import_type == "URL":
            spec_url = config.get("spec_url", "") or ""
            if not spec_url:
                check = PreflightCheck(
                    name="specUrlReachable",
                    passed=False,
                    error=InvalidInputError(
                        message="spec_url is required when import_type='URL'",
                        field="spec_url",
                        constraint="required",
                    ).to_failure_details(),
                )
            else:
                check = await _check_url_spec(spec_url)
            checks = [check]
        elif import_type == "CLOUD":
            spec_prefix = config.get("spec_prefix", "") or ""
            check = await _check_cloud_credential(list(input.credentials), spec_prefix)
            checks = [check]
        else:
            checks = [
                PreflightCheck(
                    name="importTypeKnown",
                    passed=False,
                    error=InvalidInputError(
                        message=f"Unknown import_type: {import_type!r}",
                        field="import_type",
                        constraint="must be 'URL' or 'CLOUD'",
                        value_summary=str(import_type),
                    ).to_failure_details(),
                )
            ]

        if any(not c.passed for c in checks):
            return _blocked(checks, started)
        return PreflightOutput(
            status=PreflightStatus.READY,
            checks=checks,
            message="OK",
            total_duration_ms=(perf_counter() - started) * 1000,
        )

    async def fetch_metadata(self, input: MetadataInput) -> MetadataOutput:
        """Return an empty metadata list.

        The OpenAPI connector's form uses free-text/URL inputs
        (import_type, spec_url, spec_prefix, spec_key) — no API/SQL tree
        widget that needs live population. Wired only to satisfy the
        ``Handler`` contract.
        """
        _ = input
        return ApiMetadataOutput(objects=[])
