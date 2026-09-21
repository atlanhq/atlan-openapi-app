"""Registered preflight behaviour scenarios (conformance F016).

Every test here drives the **real** ``OpenAPIConnectorHandler.preflight_check``
and validates its output with ``assert_preflight_result``. The source is faked
only at the HTTP transport seam (respx) — the app-owned source adapter the
F016 guide asks for — so the handler, its probe, its error classifier and its
verdict aggregation all execute for real.

The guide's matrix is written for a multi-resource source; this connector
probes one spec document per invocation, so two scenario names needed a
mapping decision. Both are recorded here rather than in a commit message:

* ``mixed_resources`` — the nearest thing this connector has to distinct
  resources is its two import types, so the scenario exercises the CLOUD and
  URL check sets in one test and asserts each verdict independently.
* ``extraction_fallback`` — ``app/api_client.py`` has no retry, so there is no
  in-app fallback to exercise. What the gate's fail-open actually rests on is
  probe/extraction parity: the scenario asserts the same injected 503 produces
  the same typed error from ``probe_spec_url`` and ``fetch_spec``.

Mandatory probes for this handler, declared once here rather than inferred
from names (the guide explicitly forbids guessing them):

* URL mode — ``spec_url_configured``, then ``spec_source_reachable``
* CLOUD mode — ``cloud_spec_location_configured``
* ``spec_content_type_plausible`` is advisory: it may fail without blocking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import pytest
import respx
from application_sdk.handler import (
    BaseConnectionConfig,
    PreflightInput,
)
from conformance.preflight_testing import (
    assert_preflight_result,
    assert_probe_lifetime,
)

import app.api_client as api_client_module
from app.api_client import OpenAPIApiClient
from app.errors import SpecSourceUnavailableError
from app.handler import OpenAPIConnectorHandler

SPEC_URL = "https://specs.example.com/openapi.json"

# Synthetic only — never a real signature. Used to prove the handler keeps a
# pre-signed query out of its output and its logs.
SYNTHETIC_SIG = "SyntheticSasSignature0000"
PRESIGNED_URL = f"{SPEC_URL}?sp=r&sig={SYNTHETIC_SIG}"

URL_MANDATORY = ("spec_url_configured", "spec_source_reachable")
URL_OBSERVED = {
    "spec_url_configured",
    "spec_source_reachable",
    "spec_content_type_plausible",
}
CLOUD_MANDATORY = ("cloud_spec_location_configured",)
CLOUD_OBSERVED = {"cloud_spec_location_configured"}


def _input(budget: int = 60, **config: Any) -> PreflightInput:
    return PreflightInput(
        connection_config=BaseConnectionConfig(**config),
        timeout_seconds=budget,
    )


def _json_ok() -> httpx.Response:
    return httpx.Response(
        200, content=b"{}", headers={"content-type": "application/json"}
    )


async def _run(**kwargs: Any) -> Any:
    return await OpenAPIConnectorHandler().preflight_check(_input(**kwargs))


@pytest.fixture
def closed_clients(monkeypatch: pytest.MonkeyPatch) -> list[OpenAPIApiClient]:
    """Independent teardown evidence: every client the handler closes.

    ``assert_probe_lifetime`` wants proof that a probe left no background work
    running. The handler closes its client in a ``finally``; this records each
    close so a scenario can assert it happened rather than assume it.
    """
    closed: list[OpenAPIApiClient] = []
    original = OpenAPIApiClient.close

    async def _close(self: OpenAPIApiClient) -> None:
        closed.append(self)
        await original(self)

    monkeypatch.setattr(OpenAPIApiClient, "close", _close)
    return closed


@pytest.fixture
def probe_budgets(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Every timeout the handler hands its client, in call order."""
    seen: list[float] = []
    original = OpenAPIApiClient.__init__

    def _init(self: OpenAPIApiClient, *args: Any, **kwargs: Any) -> None:
        if "timeout" in kwargs:
            seen.append(float(kwargs["timeout"]))
        original(self, *args, **kwargs)

    monkeypatch.setattr(OpenAPIApiClient, "__init__", _init)
    return seen


# =============================================================================
# Healthy and failure verdicts
# =============================================================================


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="healthy")
async def test_healthy_source_is_ready() -> None:
    """A reachable endpoint serving JSON: every probe passes, verdict READY."""
    respx.get(SPEC_URL).mock(return_value=_json_ok())

    result = await _run(import_type="URL", spec_url=SPEC_URL)

    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="ready",
        mandatory_order=URL_MANDATORY,
    )
    assert all(check.passed for check in result.checks)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="mandatory_failure")
async def test_forbidden_source_is_not_ready() -> None:
    """A 403 on the mandatory reachability probe blocks, with typed
    attribution, and short-circuits the advisory probe behind it."""
    respx.get(SPEC_URL).mock(return_value=httpx.Response(403))

    result = await _run(import_type="URL", spec_url=SPEC_URL)

    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="not_ready",
        mandatory_order=URL_MANDATORY,
        expected_errors={
            "spec_source_reachable": {
                "category": "PERMISSION",
                "code": "PERMISSION_OPENAPI_SPEC_FETCH",
                "retryable": False,
                "audience": "USER",
            }
        },
    )
    # The advisory probe never ran: reachability decided the verdict.
    assert "spec_content_type_plausible" not in {c.name for c in result.checks}


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="advisory_failure")
async def test_html_body_fails_advisory_without_blocking() -> None:
    """An HTML body is the classic false green. The advisory probe fails, is
    reported with a typed error, and the verdict stays READY."""
    respx.get(SPEC_URL).mock(
        return_value=httpx.Response(
            200, content=b"<html>login</html>", headers={"content-type": "text/html"}
        )
    )

    result = await _run(import_type="URL", spec_url=SPEC_URL)

    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="ready",
        mandatory_order=URL_MANDATORY,
    )
    advisory = next(c for c in result.checks if c.name == "spec_content_type_plausible")
    assert advisory.passed is False
    assert "spec_content_type_plausible" in result.message


# =============================================================================
# Recovery versus exhaustion
# =============================================================================


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="recoverable_transient")
async def test_transient_raises_then_clears_on_the_next_attempt() -> None:
    """A 5xx is 'ask me later', not a verdict: the handler raises a fail-open
    leaf rather than returning NOT_READY, and the next gate attempt against a
    recovered source returns READY."""
    route = respx.get(SPEC_URL)
    route.side_effect = [httpx.Response(503), _json_ok()]

    with pytest.raises(Exception) as first:
        await _run(import_type="URL", spec_url=SPEC_URL)
    # Raised, not returned — returning NOT_READY here would abort healthy runs
    # once the app opts into hard mode.
    assert first.value.__class__.__name__ == "SpecSourceTransientError"

    result = await _run(import_type="URL", spec_url=SPEC_URL)

    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="ready",
        mandatory_order=URL_MANDATORY,
    )


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="persistent_failure")
async def test_connect_failure_is_a_stable_verdict_across_attempts() -> None:
    """A connect refusal is a stable, customer-fixable fact, so it stays a
    verdict rather than a transient — and it does not clear with retries.
    Exhaustion for this connector looks like the same NOT_READY twice, not a
    fail-open."""
    respx.get(SPEC_URL).mock(side_effect=httpx.ConnectTimeout("no route"))

    for _ in range(2):
        result = await _run(import_type="URL", spec_url=SPEC_URL)
        assert_preflight_result(
            result,
            required_checks=set(URL_MANDATORY),
            observed_checks=URL_OBSERVED,
            expected_status="not_ready",
            mandatory_order=URL_MANDATORY,
        )


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="extraction_fallback")
async def test_probe_and_extraction_classify_the_same_failure_identically() -> None:
    """The gate's fail-open rests on probe/extraction parity, not on an in-app
    retry (this client has none). The same injected 503 must produce the same
    typed error from the probe and from the extraction read, or preflight would
    be judging a different failure from the one the run dies on."""
    respx.get(SPEC_URL).mock(return_value=httpx.Response(503))

    client = OpenAPIApiClient()
    try:
        with pytest.raises(Exception) as probe_error:
            await client.probe_spec_url(SPEC_URL)
        with pytest.raises(Exception) as fetch_error:
            await client.fetch_spec(SPEC_URL)
    finally:
        await client.close()

    # Pin the classification itself, not merely that the two agree: a parity
    # assertion alone would pass if both paths were wrong in the same way.
    # SOURCE_UNAVAILABLE_OPENAPI_SPEC is declared on SpecSourceUnavailableError
    # in app/errors.py — the app's own error taxonomy is the source of truth
    # here, not whatever the handler happened to emit.
    for raised in (probe_error.value, fetch_error.value):
        assert isinstance(raised, SpecSourceUnavailableError)
        assert raised.to_failure_details().code == "SOURCE_UNAVAILABLE_OPENAPI_SPEC"
    assert type(probe_error.value) is type(fetch_error.value)

    # And the handler's own output on the recovered source is truthful.
    respx.get(SPEC_URL).mock(return_value=_json_ok())
    result = await _run(import_type="URL", spec_url=SPEC_URL)
    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="ready",
        mandatory_order=URL_MANDATORY,
    )


# =============================================================================
# Resource and input shapes
# =============================================================================


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="mixed_resources")
async def test_cloud_and_url_resources_are_judged_independently() -> None:
    """This connector's two import types are its distinct resources. A healthy
    CLOUD location and a URL source with a failing advisory probe are judged
    on their own check sets — neither leaks into the other's verdict."""
    cloud = await _run(import_type="CLOUD", spec_prefix="specs/")
    assert_preflight_result(
        cloud,
        required_checks=set(CLOUD_MANDATORY),
        observed_checks=CLOUD_OBSERVED,
        expected_status="ready",
        mandatory_order=CLOUD_MANDATORY,
    )
    # PF-15: CLOUD deliberately does not claim object-store reachability, so
    # the URL path's probes must not appear here.
    assert {c.name for c in cloud.checks} == CLOUD_OBSERVED

    respx.get(SPEC_URL).mock(
        return_value=httpx.Response(
            200, content=b"<html>x</html>", headers={"content-type": "text/html"}
        )
    )
    url = await _run(import_type="URL", spec_url=SPEC_URL)
    assert_preflight_result(
        url,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="ready",
        mandatory_order=URL_MANDATORY,
    )
    assert {c.name for c in url.checks} == URL_OBSERVED


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="credential_entrypoint_shapes")
async def test_every_supported_input_shape_produces_a_typed_verdict() -> None:
    """Each manifest-supplied shape the entrypoint accepts resolves to a
    truthful verdict — including the two CLOUD spellings and the missing-field
    case, which must block rather than pass vacuously."""
    respx.get(SPEC_URL).mock(return_value=_json_ok())

    shapes = [
        (
            dict(import_type="URL", spec_url=SPEC_URL),
            "ready",
            URL_MANDATORY,
            URL_OBSERVED,
        ),
        (
            dict(import_type="CLOUD", spec_prefix="specs/"),
            "ready",
            CLOUD_MANDATORY,
            CLOUD_OBSERVED,
        ),
        (
            dict(import_type="CLOUD", spec_key="specs/openapi.json"),
            "ready",
            CLOUD_MANDATORY,
            CLOUD_OBSERVED,
        ),
        (dict(import_type="CLOUD"), "not_ready", CLOUD_MANDATORY, CLOUD_OBSERVED),
        (
            dict(import_type="URL", spec_url=""),
            "not_ready",
            URL_MANDATORY,
            URL_OBSERVED,
        ),
    ]
    for config, expected, mandatory, observed in shapes:
        result = await _run(**config)
        assert_preflight_result(
            result,
            required_checks=set(mandatory),
            observed_checks=observed,
            expected_status=expected,
            mandatory_order=mandatory,
        )


# =============================================================================
# Probe lifetime: absence, hangs, cancellation, budgets
# =============================================================================


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="no_probe")
async def test_missing_spec_url_blocks_without_probing_the_source() -> None:
    """A missing spec_url is decided from config alone. No probe is attempted,
    and the verdict is still a typed NOT_READY rather than a vacuous READY."""
    route = respx.get(SPEC_URL).mock(return_value=_json_ok())

    result = await _run(import_type="URL", spec_url="")

    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="not_ready",
        mandatory_order=URL_MANDATORY,
    )
    assert route.call_count == 0
    assert {c.name for c in result.checks} == {"spec_url_configured"}


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="hung_probe")
async def test_unanswered_probe_stays_inside_the_budget_and_cleans_up(
    closed_clients: list[OpenAPIApiClient],
    probe_budgets: list[float],
) -> None:
    """An endpoint that will not answer must not outlive the gate's budget, and
    must leave no client behind.

    The transport is faked, so the sleep-then-ReadTimeout below reproduces what
    httpx reports for a hung endpoint rather than exercising httpx's own timer.
    What this does prove against the real handler: the deadline it hands the
    client is strictly inside the enforced budget, the call returns within that
    budget, and the client is closed on the failure path.
    """
    budget = 5

    async def _hang(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        raise httpx.ReadTimeout("endpoint did not answer")

    respx.get(SPEC_URL).mock(side_effect=_hang)

    started = time.monotonic()
    with pytest.raises(Exception) as hung:
        await _run(budget=budget, import_type="URL", spec_url=SPEC_URL)
    elapsed = time.monotonic() - started

    assert hung.value.__class__.__name__ == "SpecSourceTransientError"
    assert probe_budgets and probe_budgets[0] < budget, (
        "probe deadline must sit strictly inside the enforced budget"
    )
    assert_probe_lifetime(
        elapsed=elapsed,
        budget=float(budget),
        background_stopped=bool(closed_clients),
    )

    respx.get(SPEC_URL).mock(return_value=_json_ok())
    result = await _run(budget=budget, import_type="URL", spec_url=SPEC_URL)
    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="ready",
        mandatory_order=URL_MANDATORY,
    )


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="cancellation_cleanup")
async def test_external_cancellation_propagates_and_closes_the_client(
    closed_clients: list[OpenAPIApiClient],
) -> None:
    """External cancellation must be preserved, not swallowed into a verdict,
    and must not leak the probe's client."""
    started_probe = asyncio.Event()

    async def _block(_request: httpx.Request) -> httpx.Response:
        started_probe.set()
        await asyncio.sleep(30)
        return _json_ok()

    respx.get(SPEC_URL).mock(side_effect=_block)

    budget = 5
    task = asyncio.create_task(
        _run(budget=budget, import_type="URL", spec_url=SPEC_URL)
    )
    await asyncio.wait_for(started_probe.wait(), timeout=budget)

    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - started

    assert_probe_lifetime(
        elapsed=elapsed,
        budget=float(budget),
        background_stopped=bool(closed_clients),
    )

    respx.get(SPEC_URL).mock(return_value=_json_ok())
    result = await _run(budget=budget, import_type="URL", spec_url=SPEC_URL)
    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="ready",
        mandatory_order=URL_MANDATORY,
    )


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="budget_retry")
async def test_probe_deadline_scales_with_the_remaining_gate_budget(
    closed_clients: list[OpenAPIApiClient],
    probe_budgets: list[float],
) -> None:
    """``timeout_seconds`` is the *enforced remaining* budget, so a later gate
    attempt gets a smaller one and the probe must shrink with it. A deadline
    that can exceed its budget makes the gate's cancel decorative."""
    respx.get(SPEC_URL).mock(return_value=_json_ok())

    budgets = (60, 10, 2)
    for index, budget in enumerate(budgets, start=1):
        started = time.monotonic()
        result = await _run(budget=budget, import_type="URL", spec_url=SPEC_URL)
        elapsed = time.monotonic() - started

        assert_preflight_result(
            result,
            required_checks=set(URL_MANDATORY),
            observed_checks=URL_OBSERVED,
            expected_status="ready",
            mandatory_order=URL_MANDATORY,
        )
        # Measured against THIS attempt's own remaining budget. Timing the
        # three together against their sum would pass on any timing at all —
        # only the tightest budget constrains anything, and the sum hides it.
        assert_probe_lifetime(
            elapsed=elapsed,
            budget=float(budget),
            background_stopped=len(closed_clients) == index,
        )

    assert len(probe_budgets) == len(budgets)
    for budget, probe in zip(budgets, probe_budgets):
        assert probe < budget, "probe deadline must stay inside its budget"
    assert probe_budgets == sorted(probe_budgets, reverse=True), (
        "a shrinking remaining budget must shrink the probe"
    )


# =============================================================================
# Safe typed output
# =============================================================================


@pytest.mark.asyncio
@respx.mock
@pytest.mark.preflight_conformance(rule="F016", scenario="typed_safe_output")
async def test_presigned_url_never_reaches_the_output_or_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A spec URL is routinely pre-signed, so its query string authenticates
    whoever reads it. The failure path is the dangerous one: it populates
    messages and evidence fields that travel to Temporal history, the
    Automation Engine and the connector-pulse check matrix — none of which the
    SDK redacts."""
    respx.get(url__startswith=SPEC_URL).mock(return_value=httpx.Response(403))

    with caplog.at_level(logging.DEBUG):
        result = await _run(import_type="URL", spec_url=PRESIGNED_URL)

    assert_preflight_result(
        result,
        required_checks=set(URL_MANDATORY),
        observed_checks=URL_OBSERVED,
        expected_status="not_ready",
        mandatory_order=URL_MANDATORY,
        synthetic_secrets=(SYNTHETIC_SIG,),
        captured_logs=caplog.text,
        expected_errors={
            "spec_source_reachable": {
                "category": "PERMISSION",
                "retryable": False,
                "audience": "USER",
            }
        },
    )
    assert SYNTHETIC_SIG not in result.model_dump_json()
    assert SYNTHETIC_SIG not in caplog.text
    assert api_client_module.redact_url(PRESIGNED_URL).endswith("?<redacted>")
