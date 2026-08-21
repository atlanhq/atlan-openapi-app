"""Unit tests for OpenAPIConnectorHandler.preflight_check (CONNECT-812).

The handler exists so the injected gate stops returning a vacuous READY with
zero checks (``missing_check`` on connector-pulse). These tests pin the
registry rules it was built against:

* PF-11 — a failed probe still returns a check row with a typed error.
* PF-12 — the verdict comes from mandatory checks, never ``all(c.passed)``.
* PF-18 — check messages carry no raw exception text and no raw spec URL.

Plus the two gate-semantics rules the handler must not get wrong:

* a transient (429 / 5xx / no answer) is **raised**, not returned as
  ``NOT_READY`` — the gate fails open on those categories, so returning a
  verdict would abort healthy runs once the app opts into hard mode;
* the probe's timeout stays strictly inside the enforced budget.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import respx
from application_sdk.errors import DependencyUnavailableError, RateLimitedError
from application_sdk.handler import (
    BaseConnectionConfig,
    HandlerCredential,
    PreflightInput,
    PreflightStatus,
)

from app.handler import (
    OpenAPIConnectorHandler,
    _classify_store_failure,
    _credentials_to_raw,
    _probe_timeout,
)

# DNS is stubbed suite-wide (tests/unit/conftest.py) so the SSRF check in
# app.api_client never touches a real resolver here; its own logic is tested
# directly in test_api_client.py.


def _input(**config: object) -> PreflightInput:
    return PreflightInput(
        connection_config=BaseConnectionConfig(**config),
        timeout_seconds=60,
    )


class TestUrlMode:
    @pytest.mark.asyncio
    @respx.mock
    async def test_reachable_url_is_ready(self) -> None:
        respx.get("https://example.com/api.json").mock(
            return_value=httpx.Response(
                200, content=b"{}", headers={"content-type": "application/json"}
            )
        )
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="https://example.com/api.json")
        )
        assert out.status == PreflightStatus.READY
        names = {c.name: c.passed for c in out.checks}
        assert names == {
            "spec_url_configured": True,
            "spec_source_reachable": True,
            "spec_content_type_plausible": True,
        }

    @pytest.mark.asyncio
    @respx.mock
    async def test_403_returns_not_ready_with_typed_check_row(self) -> None:
        """PF-11: the failed connect returns a populated check row — typed
        error, non-empty checks list — never an empty matrix."""
        respx.get("https://example.com/api.json").mock(return_value=httpx.Response(403))
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="https://example.com/api.json")
        )
        assert out.status == PreflightStatus.NOT_READY
        assert out.checks, "PF-11: checks must never be empty on failure"
        failed = [c for c in out.checks if not c.passed]
        assert len(failed) == 1
        assert failed[0].name == "spec_source_reachable"
        assert failed[0].error is not None
        assert failed[0].error.code == "PERMISSION_OPENAPI_SPEC_FETCH"

    @pytest.mark.asyncio
    @respx.mock
    async def test_403_on_presigned_url_does_not_leak_the_signature(self) -> None:
        """PF-18: FailureDetails.message is not redacted on the wire, so the
        query string of a pre-signed spec URL must never reach it."""
        url = "https://acct.blob.core.windows.net/specs/openapi.json"
        respx.get(url + "?sig=SUPERSECRET&se=2026-01-01").mock(
            return_value=httpx.Response(403)
        )
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(
                import_type="URL",
                spec_url=url + "?sig=SUPERSECRET&se=2026-01-01",
            )
        )
        failed = [c for c in out.checks if not c.passed][0]
        assert failed.error is not None
        rendered = failed.error.model_dump_json()
        assert "SUPERSECRET" not in rendered
        assert "sig=" not in rendered
        # ...while still naming the endpoint well enough to act on.
        assert "acct.blob.core.windows.net/specs/openapi.json" in failed.error.message

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_error_returns_not_ready_typed(self) -> None:
        respx.get("https://example.com/api.json").mock(
            side_effect=httpx.ConnectError("boom")
        )
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="https://example.com/api.json")
        )
        assert out.status == PreflightStatus.NOT_READY
        failed = [c for c in out.checks if not c.passed]
        assert failed[0].error is not None
        assert failed[0].error.code == "SOURCE_UNAVAILABLE_OPENAPI_SPEC"

    @pytest.mark.asyncio
    @respx.mock
    async def test_redirect_is_not_ready_with_its_own_code(self) -> None:
        """Redirects are not followed (the target escapes the SSRF check), so a
        3xx is a terminal answer and must not read as a generic 4xx."""
        respx.get("https://example.com/api.json").mock(
            return_value=httpx.Response(302, headers={"location": "https://x/y.json"})
        )
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="https://example.com/api.json")
        )
        assert out.status == PreflightStatus.NOT_READY
        failed = [c for c in out.checks if not c.passed][0]
        assert failed.error is not None
        assert failed.error.code == "INVALID_INPUT_OPENAPI_SPEC_REDIRECT"

    @pytest.mark.asyncio
    async def test_missing_spec_url_is_not_ready_without_probing(self) -> None:
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url="")
        )
        assert out.status == PreflightStatus.NOT_READY
        assert [c.name for c in out.checks] == ["spec_url_configured"]
        assert out.checks[0].error is not None

    @pytest.mark.asyncio
    async def test_existing_local_path_spec_url_skips_probe(
        self, tmp_path: Path
    ) -> None:
        """A scheme-less spec_url that exists is the CLOUD-downloaded artifact
        shape — fetch_spec reads it directly, so there is nothing to probe."""
        spec = tmp_path / "spec.json"
        spec.write_bytes(b"{}")
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url=str(spec))
        )
        assert out.status == PreflightStatus.READY
        reach = [c for c in out.checks if c.name == "spec_source_reachable"][0]
        assert reach.passed is True
        assert "skipped" in reach.message

    @pytest.mark.asyncio
    async def test_missing_local_path_spec_url_is_not_ready(
        self, tmp_path: Path
    ) -> None:
        """A scheme-less spec_url that does not exist fails the run
        deterministically: fetch_spec stats it, misses, falls through to the
        HTTP path, and validate_spec_url rejects the non-HTTPS string. Green
        here would be the false green the handler exists to remove — and the
        leaf must match the one extraction raises for the same input."""
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url=str(tmp_path / "nope.json"))
        )
        assert out.status == PreflightStatus.NOT_READY
        reach = [c for c in out.checks if c.name == "spec_source_reachable"][0]
        assert reach.passed is False
        assert reach.error is not None
        assert reach.error.code == "INVALID_INPUT_OPENAPI_SPEC_URL_INVALID"
        assert reach.error.suggested_action
        # PF-18: the raw path never reaches the wire-visible message.
        assert str(tmp_path) not in reach.error.message
        # The advisory content-type row cannot run without a response.
        assert not [c for c in out.checks if c.name == "spec_content_type_plausible"]


class TestTransientsAreRaisedNotVoted:
    """A transient means 'ask me later', not 'the source is not ready'.

    The gate treats RATE_LIMITED / DEPENDENCY_UNAVAILABLE as its own plumbing
    and fails open on them in both postures. Returning NOT_READY instead would
    abort a healthy run on a spec-host blip the moment the app goes hard.
    """

    URL = "https://example.com/api.json"

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_raises_rate_limited(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(429))
        with pytest.raises(RateLimitedError):
            await OpenAPIConnectorHandler().preflight_check(
                _input(import_type="URL", spec_url=self.URL)
            )

    @pytest.mark.asyncio
    @respx.mock
    async def test_5xx_raises_dependency_unavailable(self) -> None:
        respx.get(self.URL).mock(return_value=httpx.Response(503))
        with pytest.raises(DependencyUnavailableError) as excinfo:
            await OpenAPIConnectorHandler().preflight_check(
                _input(import_type="URL", spec_url=self.URL)
            )
        assert excinfo.value.code == "DEPENDENCY_UNAVAILABLE_OPENAPI_SPEC_SOURCE"

    @pytest.mark.asyncio
    @respx.mock
    async def test_read_timeout_raises_dependency_unavailable(self) -> None:
        respx.get(self.URL).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(DependencyUnavailableError):
            await OpenAPIConnectorHandler().preflight_check(
                _input(import_type="URL", spec_url=self.URL)
            )

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_timeout_is_a_verdict_not_a_transient(self) -> None:
        """Connectivity is the canonical NOT_READY tier — a refused or
        unroutable host is a stable, customer-fixable fact."""
        respx.get(self.URL).mock(side_effect=httpx.ConnectTimeout("no route"))
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url=self.URL)
        )
        assert out.status == PreflightStatus.NOT_READY


class TestAdvisoryCheck:
    URL = "https://example.com/api.json"

    @pytest.mark.asyncio
    @respx.mock
    async def test_html_response_is_partial_not_blocking(self) -> None:
        """PF-12: an advisory failure downgrades to PARTIAL and the run
        proceeds — it must never produce NOT_READY."""
        respx.get(self.URL).mock(
            return_value=httpx.Response(
                200,
                content=b"<html>login</html>",
                headers={"content-type": "text/html"},
            )
        )
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url=self.URL)
        )
        assert out.status == PreflightStatus.PARTIAL
        advisory = [c for c in out.checks if c.name == "spec_content_type_plausible"][0]
        assert advisory.passed is False
        assert advisory.error is not None
        reachable = [c for c in out.checks if c.name == "spec_source_reachable"][0]
        assert reachable.passed is True

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_content_type_does_not_trip_the_advisory(self) -> None:
        """Plenty of endpoints serve a good spec with no content-type; a false
        advisory failure is worse than no advisory check."""
        respx.get(self.URL).mock(return_value=httpx.Response(200, content=b"{}"))
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="URL", spec_url=self.URL)
        )
        assert out.status == PreflightStatus.READY


class TestCloudMode:
    @pytest.mark.asyncio
    async def test_location_configured_is_ready(self) -> None:
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="CLOUD", spec_prefix="specs", spec_key="a.json")
        )
        assert out.status == PreflightStatus.READY
        # PF-15: the check row states what is NOT probed.
        assert "not probed" in out.checks[0].message

    @pytest.mark.asyncio
    async def test_missing_location_is_not_ready(self) -> None:
        out = await OpenAPIConnectorHandler().preflight_check(
            _input(import_type="CLOUD", spec_prefix="", spec_key="")
        )
        assert out.status == PreflightStatus.NOT_READY
        assert out.checks[0].error is not None
        assert out.checks[0].error.code == (
            "INVALID_INPUT_OPENAPI_CLOUD_SPEC_LOCATION_REQUIRED"
        )


class TestCloudCredentialProbe:
    """The CLOUD path's object-store credential probe.

    Motivated by a production run whose gate returned READY and then failed in
    ``download_cloud_spec`` on an AssumeRole rejection: the readiness surface
    the gate reported did not include the credential extraction depends on.
    """

    @staticmethod
    def _cloud_input(**creds: list[HandlerCredential]) -> PreflightInput:
        return PreflightInput(
            connection_config=BaseConnectionConfig(
                import_type="CLOUD", spec_prefix="specs", spec_key="a.json"
            ),
            timeout_seconds=60,
            credentials_by_name=creds,
        )

    @staticmethod
    def _creds() -> list[HandlerCredential]:
        return [
            HandlerCredential(key="authType", value="s3"),
            HandlerCredential(key="extra.aws_role_arn", value="arn:aws:iam::1:role/r"),
        ]

    @pytest.mark.asyncio
    async def test_readable_store_is_ready(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _ok(raw: dict, prefix: str, key: str) -> None:
            assert prefix == "specs"
            assert key == "a.json"

        monkeypatch.setattr("app.handler._read_object_store", _ok)
        out = await OpenAPIConnectorHandler().preflight_check(
            self._cloud_input(object_store=self._creds())
        )
        assert out.status == PreflightStatus.READY
        assert {c.name: c.passed for c in out.checks}["cloud_spec_credential_valid"]

    @pytest.mark.asyncio
    async def test_unresolved_credential_does_not_false_fail(self) -> None:
        """An empty group means "no credential reached the gate", which the gate
        also returns for a trigger carrying no metadata. Failing on it would
        block runs whose credential extraction resolves perfectly well."""
        out = await OpenAPIConnectorHandler().preflight_check(self._cloud_input())
        assert out.status == PreflightStatus.READY
        row = next(c for c in out.checks if c.name == "cloud_spec_credential_valid")
        assert row.passed
        assert "not probed" in row.message

    @pytest.mark.parametrize(
        ("exc", "code"),
        [
            ("UnauthenticatedError", "AUTH_OPENAPI_CLOUD_SPEC_CREDENTIAL"),
            ("PermissionDeniedError", "PERMISSION_OPENAPI_CLOUD_SPEC"),
            ("NotFoundError", "NOT_FOUND_OPENAPI_CLOUD_SPEC"),
        ],
    )
    @pytest.mark.asyncio
    async def test_attributable_failure_is_not_ready_and_typed(
        self, monkeypatch: pytest.MonkeyPatch, exc: str, code: str
    ) -> None:
        import obstore.exceptions as obstore_exceptions

        raised = getattr(obstore_exceptions, exc)("store said no")

        async def _fail(raw: dict, prefix: str, key: str) -> None:
            raise raised

        monkeypatch.setattr("app.handler._read_object_store", _fail)
        out = await OpenAPIConnectorHandler().preflight_check(
            self._cloud_input(object_store=self._creds())
        )
        assert out.status == PreflightStatus.NOT_READY
        row = next(c for c in out.checks if c.name == "cloud_spec_credential_valid")
        assert row.passed is False
        assert row.error is not None
        assert row.error.code == code

    @pytest.mark.asyncio
    async def test_wrapped_cause_is_still_classified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The SDK wraps object-store failures in StorageError, whose leaf is
        PLATFORM. Classification must read the cause, or the probe fails open on
        the exact case it exists to catch."""
        from application_sdk.storage.errors import StorageError
        from obstore.exceptions import UnauthenticatedError

        async def _fail(raw: dict, prefix: str, key: str) -> None:
            try:
                raise UnauthenticatedError("assume role rejected")
            except UnauthenticatedError as inner:
                raise StorageError("Failed to head key", cause=inner) from inner

        monkeypatch.setattr("app.handler._read_object_store", _fail)
        out = await OpenAPIConnectorHandler().preflight_check(
            self._cloud_input(object_store=self._creds())
        )
        assert out.status == PreflightStatus.NOT_READY
        row = next(c for c in out.checks if c.name == "cloud_spec_credential_valid")
        assert row.error is not None
        assert row.error.code == "AUTH_OPENAPI_CLOUD_SPEC_CREDENTIAL"
        assert row.error.audience.value == "USER"
        assert row.error.retryable is False

    @pytest.mark.asyncio
    async def test_unattributable_failure_is_raised_not_voted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _fail(raw: dict, prefix: str, key: str) -> None:
            raise RuntimeError("something else entirely")

        monkeypatch.setattr("app.handler._read_object_store", _fail)
        with pytest.raises(DependencyUnavailableError) as caught:
            await OpenAPIConnectorHandler().preflight_check(
                self._cloud_input(object_store=self._creds())
            )
        assert caught.value.code == "DEPENDENCY_UNAVAILABLE_OPENAPI_CLOUD_SPEC_STORE"

    @pytest.mark.asyncio
    async def test_overrun_is_a_transient_not_a_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _hang(raw: dict, prefix: str, key: str) -> None:
            await asyncio.sleep(10)

        monkeypatch.setattr("app.handler._read_object_store", _hang)
        monkeypatch.setattr("app.handler._probe_timeout", lambda _budget: 0.01)
        with pytest.raises(DependencyUnavailableError):
            await OpenAPIConnectorHandler().preflight_check(
                self._cloud_input(object_store=self._creds())
            )

    @pytest.mark.asyncio
    async def test_missing_location_short_circuits_the_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(raw: dict, prefix: str, key: str) -> None:
            raise AssertionError("probe must not run when the location is invalid")

        monkeypatch.setattr("app.handler._read_object_store", _boom)
        out = await OpenAPIConnectorHandler().preflight_check(
            PreflightInput(
                connection_config=BaseConnectionConfig(
                    import_type="CLOUD", spec_prefix="", spec_key=""
                ),
                timeout_seconds=60,
                credentials_by_name={"object_store": TestCloudCredentialProbe._creds()},
            )
        )
        assert out.status == PreflightStatus.NOT_READY
        assert [c.name for c in out.checks] == ["cloud_spec_location_configured"]

    @pytest.mark.asyncio
    async def test_inline_credentials_are_probed_on_the_ui_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test-Connection sends credentials inline, not through the gate's named
        ref. Without the fallback the probe silently reports "not probed" on the
        one surface a user watches interactively."""
        seen: dict[str, object] = {}

        async def _capture(raw: dict, prefix: str, key: str) -> None:
            seen["raw"] = raw

        monkeypatch.setattr("app.handler._read_object_store", _capture)
        out = await OpenAPIConnectorHandler().preflight_check(
            PreflightInput(
                connection_config=BaseConnectionConfig(
                    import_type="CLOUD", spec_prefix="specs", spec_key="a.json"
                ),
                timeout_seconds=60,
                credentials=self._creds(),
            )
        )
        assert out.status == PreflightStatus.READY
        assert seen["raw"] == {
            "authType": "s3",
            "extra": {"aws_role_arn": "arn:aws:iam::1:role/r"},
        }

    def test_flat_pairs_rebuild_the_nested_extra(self) -> None:
        raw = _credentials_to_raw(
            [
                HandlerCredential(key="authType", value="s3"),
                HandlerCredential(key="extra.aws_role_arn", value="arn"),
                HandlerCredential(key="extra.region", value="ap-southeast-2"),
            ]
        )
        assert raw == {
            "authType": "s3",
            "extra": {"aws_role_arn": "arn", "region": "ap-southeast-2"},
        }

    def test_severed_context_is_not_walked(self) -> None:
        """A `raise ... from None` upstream means that context is not the cause.
        Walking it anyway would attribute an unrelated earlier failure to the
        credential."""
        from obstore.exceptions import UnauthenticatedError

        try:
            raise UnauthenticatedError("an unrelated earlier failure")
        except UnauthenticatedError:
            severed = RuntimeError("the actual failure")
            try:
                raise severed from None
            except RuntimeError as exc:
                assert exc.__context__ is not None
                assert _classify_store_failure(exc) is None

    def test_unknown_failure_stays_unattributed(self) -> None:
        """None is what routes to fail-open. Guessing here would make the gate
        fail closed on an object-store outage."""
        assert _classify_store_failure(RuntimeError("who knows")) is None


class TestNamedCredentialRef:
    def test_gate_resolves_the_object_store_credential(self) -> None:
        """``cloud_source`` is the CLOUD path's credential widget, so the guid
        arrives there and never on the top-level triple. Without the declared
        ref the gate resolves nothing and the probe can never run."""
        from application_sdk.execution._temporal.preflight_gate import (
            PreflightGateInput,
        )

        from app.contracts import OpenAPIConnectorInput

        gate_input = PreflightGateInput.from_extraction_input(
            OpenAPIConnectorInput.model_validate(
                {
                    "workflow_id": "w",
                    "import_type": "CLOUD",
                    "spec_key": "a.json",
                    "cloud_source": "some-guid",
                }
            ),
            "crawler",
        )
        assert gate_input.credential_ref_fields == {"object_store": "cloud_source"}
        assert gate_input.extraction_snapshot["cloud_source"] == "some-guid"

    def test_ref_map_is_a_classvar_not_a_field(self) -> None:
        """Declared as a pydantic field the gate reads {} and silently falls
        back to single-credential resolution."""
        from app.contracts import OpenAPIConnectorInput

        assert "preflight_credential_refs" not in OpenAPIConnectorInput.model_fields


class TestProbeTimeout:
    def test_always_strictly_inside_the_enforced_budget(self) -> None:
        assert _probe_timeout(150) == 30.0  # ceiling
        assert _probe_timeout(60) == 30.0  # ceiling
        assert _probe_timeout(20) == 16.0  # 80% of the remaining budget
        # The old floor returned 5.0 here — larger than the budget itself,
        # which makes the enforced deadline decorative.
        assert _probe_timeout(6) == pytest.approx(4.8)
        assert _probe_timeout(1) == 1.0


class TestGateWiring:
    def test_handler_is_discoverable_from_the_app_module(self) -> None:
        """The gate finds the handler by convention ({AppClassName}Handler in
        the App class's module), which this app satisfies with a re-export in
        app/connector.py. Without this test an import cleanup silently drops
        every check back to the SDK no-op handler and nothing fails."""
        from application_sdk.discovery import load_handler_class

        assert (
            load_handler_class("app.connector:OpenAPIConnector")
            is OpenAPIConnectorHandler
        )
