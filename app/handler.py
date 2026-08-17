"""Preflight handler for the OpenAPI Spec Loader connector.

CONNECT-812 context: before this handler existed the injected preflight gate
ran the SDK's no-op handler, which returns READY with **zero checks**. On
connector-pulse that shows as ``missing_check`` / ``coverage_gap`` for every
run — and the gate stayed green while the daily scheduled run on a real tenant
failed at extract with an HTTP 403 from the spec source. A vacuously green
gate is no evidence of source readiness, so this handler probes exactly what
``extract_spec`` reads.

Design rules applied from the CONNECT-812 pattern registry:

* **PF-11** — a failed connect still returns a check *row* (never an empty
  ``checks`` list), so ``check_matrix``, the typed reason code, and the
  activity pane stay populated.
* **PF-12** — the verdict is computed from mandatory checks only, never
  ``all(c.passed)``, so an advisory failure can downgrade to PARTIAL but can
  never block a run.
* **PF-15 / PF-19** — the probe requests the same document over the same
  client configuration the extraction path uses; what it cannot see (object
  store contents on the CLOUD path) is stated, not implied.
* **PF-18** — check messages never interpolate raw exception text; failures
  travel as typed ``FailureDetails`` whose ``cause_repr`` is redacted by the
  SDK.
"""

from __future__ import annotations

import time

import httpx
from application_sdk.errors.base import AppError
from application_sdk.handler import (
    DefaultHandler,
    PreflightCheck,
    PreflightInput,
    PreflightOutput,
    PreflightStatus,
)
from application_sdk.observability.logger_adaptor import get_logger

from app.api_client import _classify_http_status, validate_spec_url
from app.errors import (
    CloudSpecLocationRequiredError,
    SpecSourceUnavailableError,
    SpecUrlInvalidError,
    SpecUrlRequiredError,
)

logger = get_logger(__name__)

# Checks whose failure means the run is deterministically going to fail.
# Everything else is advisory and can only downgrade the verdict to PARTIAL.
_MANDATORY_CHECKS = frozenset(
    {
        "spec_url_configured",
        "spec_source_reachable",
        "cloud_spec_location_configured",
    }
)

# Ceiling for the URL probe. input.timeout_seconds carries the enforced
# remaining gate budget; the probe must finish inside it with headroom.
_PROBE_MAX_SECONDS = 30.0
_PROBE_MIN_SECONDS = 5.0
_PROBE_BUDGET_HEADROOM_SECONDS = 5.0


def _probe_timeout(budget_seconds: int) -> float:
    """Size the probe to the real enforced deadline, with headroom."""
    return max(
        _PROBE_MIN_SECONDS,
        min(_PROBE_MAX_SECONDS, float(budget_seconds) - _PROBE_BUDGET_HEADROOM_SECONDS),
    )


class OpenAPIConnectorHandler(DefaultHandler):
    """Preflight checks for the OpenAPI Spec Loader.

    Discovered by convention (``{AppClassName}Handler``) via the re-export in
    ``app.connector``. ``test_auth`` and ``fetch_metadata`` inherit the
    DefaultHandler pass-throughs — this connector has no interactive auth or
    metadata tree.
    """

    async def preflight_check(self, input: PreflightInput) -> PreflightOutput:
        started = time.monotonic()
        cfg = input.connection_config
        import_type = str(cfg.get("import_type") or "URL").upper()

        checks: list[PreflightCheck] = []
        if import_type == "CLOUD":
            checks.append(self._check_cloud_location(cfg))
        else:
            spec_url = str(cfg.get("spec_url") or "")
            configured = self._check_spec_url_configured(spec_url)
            checks.append(configured)
            if configured.passed:
                checks.append(
                    await self._check_spec_source_reachable(
                        spec_url, _probe_timeout(input.timeout_seconds)
                    )
                )

        # PF-12: mandatory checks decide the verdict; advisory failures only
        # downgrade READY to PARTIAL.
        mandatory_failed = any(
            not c.passed for c in checks if c.name in _MANDATORY_CHECKS
        )
        advisory_failed = any(
            not c.passed for c in checks if c.name not in _MANDATORY_CHECKS
        )
        if mandatory_failed:
            status = PreflightStatus.NOT_READY
        elif advisory_failed:
            status = PreflightStatus.PARTIAL
        else:
            status = PreflightStatus.READY

        failed = [c.name for c in checks if not c.passed]
        return PreflightOutput(
            status=status,
            checks=checks,
            message=(
                f"{len(failed)} of {len(checks)} check(s) failed: " + ", ".join(failed)
                if failed
                else ""
            ),
            total_duration_ms=(time.monotonic() - started) * 1000.0,
        )

    # ------------------------------------------------------------------
    # Individual checks — each ALWAYS returns a row (PF-11)
    # ------------------------------------------------------------------

    def _check_spec_url_configured(self, spec_url: str) -> PreflightCheck:
        """URL mode: spec_url must be present — its absence fails the run
        deterministically at extract_spec."""
        if spec_url:
            return PreflightCheck(name="spec_url_configured", passed=True)
        return PreflightCheck(
            name="spec_url_configured",
            passed=False,
            error=SpecUrlRequiredError(
                message="spec_url is required when import_type='URL'",
                field="spec_url",
                constraint="required when import_type='URL'",
            ).to_failure_details(),
        )

    def _check_cloud_location(self, cfg: object) -> PreflightCheck:
        """CLOUD mode: spec_prefix or spec_key must be present.

        Static only — this handler deliberately does NOT claim object-store
        reachability (PF-15: an unprobed surface is stated as unprobed, not
        implied green by a weaker check). The location check still predicts a
        deterministic failure: run() rejects a CLOUD input with neither field.
        """
        get = getattr(cfg, "get", None)
        spec_prefix = str(get("spec_prefix") or "") if get else ""
        spec_key = str(get("spec_key") or "") if get else ""
        if spec_prefix or spec_key:
            return PreflightCheck(
                name="cloud_spec_location_configured",
                passed=True,
                message=(
                    "spec location configured; object-store reachability is "
                    "not probed by preflight and is verified at run time"
                ),
            )
        return PreflightCheck(
            name="cloud_spec_location_configured",
            passed=False,
            error=CloudSpecLocationRequiredError(
                message="spec_prefix or spec_key required when import_type='CLOUD'",
                field="spec_prefix|spec_key",
                constraint="at least one is required when import_type='CLOUD'",
            ).to_failure_details(),
        )

    async def _check_spec_source_reachable(
        self, spec_url: str, timeout_seconds: float
    ) -> PreflightCheck:
        """URL mode: request the same document extract_spec will fetch.

        A streaming GET aborted after the status line — the server sees the
        identical request the extraction path sends (same Accept header, same
        redirect policy), so a 403 here is the same 403 the run would die on,
        attributed pre-run instead. The body is never downloaded, so a large
        spec cannot eat the gate budget.
        """
        started = time.monotonic()
        check_name = "spec_source_reachable"
        # Local-file spec "URLs" (CLOUD-downloaded artifacts) never reach this
        # check — it runs only in URL mode — but guard anyway rather than probe
        # a non-URL string.
        if "://" not in spec_url:
            return PreflightCheck(
                name=check_name,
                passed=True,
                message="spec_url is a local path; reachability probe skipped",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        headers = {"Accept": "application/json, application/yaml, text/yaml, */*"}
        try:
            validate_spec_url(spec_url)
        except SpecUrlInvalidError as exc:
            return PreflightCheck(
                name=check_name,
                passed=False,
                error=exc.to_failure_details(),
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        try:
            async with (
                httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout_seconds,
                    follow_redirects=False,
                ) as client,
                client.stream("GET", spec_url) as response,
            ):
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = _classify_http_status(exc.response.status_code, spec_url, exc)
            return PreflightCheck(
                name=check_name,
                passed=False,
                error=error.to_failure_details(),
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return PreflightCheck(
                name=check_name,
                passed=False,
                error=SpecSourceUnavailableError(
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
                ).to_failure_details(),
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        except httpx.TimeoutException as exc:
            # EP-02: reached but slow is not a connectivity problem.
            return PreflightCheck(
                name=check_name,
                passed=False,
                error=SpecSourceUnavailableError(
                    message=(
                        f"connected to spec endpoint {spec_url} but timed out "
                        f"waiting for the response ({type(exc).__name__})"
                    ),
                    endpoint=spec_url,
                    network_error=type(exc).__name__,
                    suggested_action=(
                        "The endpoint is reachable but slow to answer; retry, "
                        "or check the spec server's load."
                    ),
                    cause=exc,
                ).to_failure_details(),
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        except httpx.RequestError as exc:
            return PreflightCheck(
                name=check_name,
                passed=False,
                error=SpecSourceUnavailableError(
                    message=(
                        f"network error probing spec endpoint {spec_url} "
                        f"({type(exc).__name__})"
                    ),
                    endpoint=spec_url,
                    network_error=type(exc).__name__,
                    cause=exc,
                ).to_failure_details(),
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        except AppError as exc:
            return PreflightCheck(
                name=check_name,
                passed=False,
                error=exc.to_failure_details(),
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        return PreflightCheck(
            name=check_name,
            passed=True,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )
