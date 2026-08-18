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
* **PF-18** — check messages never interpolate raw exception text, and never
  the raw spec URL (which is routinely pre-signed); failures travel as typed
  ``FailureDetails`` whose ``cause_repr`` is redacted by the SDK.

**Verdicts versus transients.** A ``NOT_READY`` verdict means *the source is
not ready* — something the connection owner must fix. A rate limit, a 5xx, or
an endpoint that will not answer is "ask me later", not a verdict: those are
**raised** as ``RATE_LIMITED`` / ``DEPENDENCY_UNAVAILABLE`` leaves, which the
gate classifies as its own plumbing and fails open on. Collapsing the two would
make hard mode abort a healthy run whenever the spec host hiccups.
"""

from __future__ import annotations

import time

from application_sdk.errors import RateLimitedError
from application_sdk.errors.base import AppError
from application_sdk.handler import (
    BaseConnectionConfig,
    DefaultHandler,
    PreflightCheck,
    PreflightInput,
    PreflightOutput,
    PreflightStatus,
)
from application_sdk.observability.logger_adaptor import get_logger

from app.api_client import OpenAPIApiClient, redact_url
from app.errors import (
    CloudSpecLocationRequiredError,
    SpecFetchClientError,
    SpecSourceTransientError,
    SpecSourceUnavailableError,
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

# Ceiling for the URL probe, and the share of the remaining gate budget it may
# consume. ``input.timeout_seconds`` carries the *enforced* remaining budget, so
# the probe's own timeout must stay strictly inside it — a floor that can exceed
# the budget makes the deadline decorative and turns a slow endpoint into a
# handler overrun, which hard mode treats as an unverifiable source.
_PROBE_MAX_SECONDS = 30.0
_PROBE_BUDGET_FRACTION = 0.8


def _probe_timeout(budget_seconds: int) -> float:
    """Size the probe to the real enforced deadline, always strictly inside it."""
    return max(
        1.0, min(_PROBE_MAX_SECONDS, float(budget_seconds) * _PROBE_BUDGET_FRACTION)
    )


# httpx failure classes that mean "reached it, couldn't finish" rather than
# "couldn't get there". A connect refusal or a DNS failure stays a verdict — the
# canonical connectivity tier — because those are stable, customer-fixable facts
# (firewall, wrong host). These are not.
_TRANSIENT_NETWORK_ERRORS = frozenset(
    {
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
    }
)


def _as_gate_transient(error: AppError) -> AppError | None:
    """The fail-open error to raise instead of a ``NOT_READY`` verdict, or None.

    The extraction path types a 5xx and a read timeout as
    ``SpecSourceUnavailableError`` (USER audience) — correct there, because the
    run really did fail and the connection owner is the one who chases the spec
    host. On the *preflight* path the same condition means only "could not be
    evaluated this time", so it is remapped onto a ``DEPENDENCY_UNAVAILABLE``
    leaf, which the gate routes to fail-open in both postures. One root cause,
    two surfaces, each attributed for what actually happened on it.
    """
    if isinstance(error, RateLimitedError):
        return error
    if not isinstance(error, SpecSourceUnavailableError):
        return None
    http_status = getattr(error, "http_status", None)
    network_error = getattr(error, "network_error", "") or ""
    if http_status:
        reason = f"HTTP {http_status}"
    elif network_error in _TRANSIENT_NETWORK_ERRORS:
        reason = network_error
    else:
        return None
    return SpecSourceTransientError(
        message=(
            "spec endpoint could not be evaluated during preflight "
            f"({reason}); treating as a transient rather than a verdict"
        ),
        service="openapi_spec_endpoint",
        cause=error,
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
                checks.extend(
                    await self._check_spec_source(
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

    def _check_cloud_location(self, cfg: BaseConnectionConfig) -> PreflightCheck:
        """CLOUD mode: spec_prefix or spec_key must be present.

        Static only — this handler deliberately does NOT claim object-store
        reachability (PF-15: an unprobed surface is stated as unprobed, not
        implied green by a weaker check). The location check still predicts a
        deterministic failure: run() rejects a CLOUD input with neither field.
        """
        spec_prefix = str(cfg.get("spec_prefix") or "")
        spec_key = str(cfg.get("spec_key") or "")
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

    async def _check_spec_source(
        self, spec_url: str, timeout_seconds: float
    ) -> list[PreflightCheck]:
        """URL mode: request the same document extract_spec will fetch.

        Runs through :class:`~app.api_client.OpenAPIApiClient` rather than a
        second hand-rolled client, so the probe cannot drift from extraction:
        one URL validator, one header set, one redirect policy, one error
        classifier.

        Returns the mandatory ``spec_source_reachable`` row, plus — when the
        endpoint answered — the advisory ``spec_content_type_plausible`` row.

        Raises:
            AppError: For a transient (rate limit, 5xx, no answer). Raising
                rather than returning ``NOT_READY`` is deliberate; see the
                module docstring.
        """
        started = time.monotonic()
        check_name = "spec_source_reachable"
        # Local-file spec "URLs" (CLOUD-downloaded artifacts) never reach this
        # check — it runs only in URL mode — but guard anyway rather than probe
        # a non-URL string.
        if "://" not in spec_url:
            return [
                PreflightCheck(
                    name=check_name,
                    passed=True,
                    message="spec_url is a local path; reachability probe skipped",
                    duration_ms=(time.monotonic() - started) * 1000.0,
                )
            ]

        client = OpenAPIApiClient(timeout=timeout_seconds)
        try:
            content_type = await client.probe_spec_url(spec_url)
        except AppError as exc:
            transient = _as_gate_transient(exc)
            if transient is not None:
                # Not a verdict — the source could not be evaluated this time.
                # The gate fails open on these categories in both postures.
                raise transient from exc
            return [
                PreflightCheck(
                    name=check_name,
                    passed=False,
                    error=exc.to_failure_details(),
                    duration_ms=(time.monotonic() - started) * 1000.0,
                )
            ]
        finally:
            await client.close()

        return [
            PreflightCheck(
                name=check_name,
                passed=True,
                duration_ms=(time.monotonic() - started) * 1000.0,
            ),
            self._check_content_type_plausible(spec_url, content_type),
        ]

    def _check_content_type_plausible(
        self, spec_url: str, content_type: str
    ) -> PreflightCheck:
        """Advisory: a 200 that serves HTML is almost never a spec document.

        The classic false-green — an SSO redirect landing page, or a portal's
        "not found" page — answers 200 with ``text/html``, so reachability
        alone says ready while extraction dies at parse. Advisory rather than
        mandatory because the reverse mistake is worse: plenty of endpoints
        serve a perfectly good spec as ``text/plain``, ``application/octet-
        stream``, or with no content-type at all, and none of those should
        block a run. Only an explicit HTML content-type trips it.
        """
        name = "spec_content_type_plausible"
        if "html" not in content_type:
            return PreflightCheck(name=name, passed=True)
        return PreflightCheck(
            name=name,
            passed=False,
            error=SpecFetchClientError(
                message=(
                    "spec endpoint answered with HTML "
                    f"(content-type {content_type!r}) for {redact_url(spec_url)}; "
                    "this is usually a login or error page rather than a spec "
                    "document"
                ),
                field="spec_url",
                constraint="endpoint should serve an OpenAPI JSON or YAML document",
                value_summary="content-type: html",
                suggested_action=(
                    "Open the spec URL and confirm it returns the raw document "
                    "rather than an HTML page. If the endpoint requires sign-in, "
                    "use a URL that does not."
                ),
            ).to_failure_details(),
        )


__all__ = ["OpenAPIConnectorHandler"]
