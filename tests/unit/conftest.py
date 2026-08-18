"""Shared fixtures for the unit suite."""

from __future__ import annotations

import pytest

# A public, non-blocked address. The SSRF check in app.api_client resolves the
# spec URL's hostname before every fetch and every preflight probe, so without
# this stub the "no real network calls are made" unit suite would perform real
# DNS — failing offline, and adding resolver latency to every URL-mode test.
# The check's own logic is tested directly in test_api_client.py by stubbing
# this same seam with the addresses each case needs.
PUBLIC_ADDRESS = "93.184.216.34"


@pytest.fixture(autouse=True)
def stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_hostname: str, _port: int | None) -> list[str]:
        return [PUBLIC_ADDRESS]

    monkeypatch.setattr("app.api_client._resolve_host", _resolve)
