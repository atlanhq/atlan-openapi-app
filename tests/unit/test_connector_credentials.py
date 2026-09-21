"""Guards the SDK floor that app/connector.py's credential handling relies on.

``pyproject.toml`` floors ``atlan-application-sdk`` at 3.29.0 for a security
reason, not a feature one: that release introduced ``ENABLE_LOG_DIAGNOSE`` and
defaulted loguru's ``diagnose`` to False. Below it the SDK takes loguru's own
default of True, which annotates every traceback frame with the *values* of the
names on its source line — so any traceback through a frame holding a resolved
credential renders that credential onto the console.

A floor is only as good as something that notices when it slips, hence this
file. It does NOT justify chaining the object-store credential failure: that
chain stays severed for a different reason (the cause's own message, which
``diagnose`` does not govern), pinned by
``test_connector.py::test_rejected_credential_raises_typed_with_severed_chain``.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from application_sdk.errors.base import AppError
from application_sdk.storage.cloud import CloudStore

# Synthetic throughout — never a real key.
SECRET = "SyntheticSecretKey0000AAAA"


def _creds(**extra_fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"username": "AKIASYNTHETIC", "password": SECRET}
    base.update(extra_fields)
    return base


# Shapes that raise a *typed* SDK error. app/connector.py catches these in its
# earlier `except AppError: raise`, so they never reach the broad catch below
# it. Listed to pin how small that broad catch's real surface is — every
# failure from_credentials documents lands here, not there.
TYPED_SHAPES = {
    "s3 missing bucket": _creds(authType="s3", extra={}),
    "unknown auth type": _creds(authType="quantum", extra={}),
    "adls missing account": _creds(authType="adls", extra={"adls_container": "c"}),
}


def test_log_diagnose_is_off_by_default() -> None:
    """Frame-variable rendering must stay off.

    Importing the constant at all asserts the SDK is >= 3.29.0, where it was
    introduced; its value asserts nobody has changed the default. If this
    fails, either the floor in pyproject.toml slipped or the SDK flipped the
    default, and every traceback through a credential-bearing frame is
    rendering values again.

    Skipped rather than failed when the env var is explicitly set: the SDK's
    own docstring suggests enabling it for local debugging, and punishing a
    developer who took that advice would teach them to delete this test. CI
    never sets it, which is where the guard has to hold.
    """
    from application_sdk.constants import ENABLE_LOG_DIAGNOSE

    if "ATLAN_LOG_DIAGNOSE" in os.environ:
        pytest.skip("ATLAN_LOG_DIAGNOSE is set explicitly; the default is not in play")
    assert ENABLE_LOG_DIAGNOSE is False


@pytest.mark.parametrize("name", sorted(TYPED_SHAPES))
def test_documented_failures_stay_typed_and_skip_the_broad_catch(name: str) -> None:
    """Every documented from_credentials failure is an AppError, so the broad
    `except Exception` in download_cloud_spec only ever sees an undocumented
    escape. If one of these stops being typed, that catch's surface grew."""
    with pytest.raises(AppError) as raised:
        CloudStore.from_credentials(TYPED_SHAPES[name])
    assert SECRET not in str(raised.value)
