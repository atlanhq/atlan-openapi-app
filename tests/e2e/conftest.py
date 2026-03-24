"""Pytest fixtures for OpenAPI connector K8s e2e tests.

Prerequisites:
  1. A kubectl context pointing at a cluster with Temporal + DAPR + KEDA installed.
  2. Secrets required by the app present in the cluster (see helm/values.yaml).

  No E2E_* environment variables are required — everything is in helm/values.yaml.

Run:
  uv run pytest tests/e2e/ -v --log-cli-level=INFO
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# tests/e2e/conftest.py -> e2e -> tests -> repo root -> .. -> application-sdk
_REPO_ROOT = Path(__file__).parents[2]
_CHART_PATH = _REPO_ROOT.parent / "application-sdk" / "helm" / "atlan-app"
_VALUES_FILE = _REPO_ROOT / "helm" / "values.yaml"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_APP_NAME = "openapi"
_NAMESPACE = "app-openapi"
_HANDLER_PORT = 80  # Service port (-> container 8000)
_DEPLOY_TIMEOUT = 300  # seconds


@dataclass
class DeployedApp:
    namespace: str
    app_name: str
    handler_port: int = _HANDLER_PORT


# ---------------------------------------------------------------------------
# Session fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def deployed_app() -> AsyncGenerator[DeployedApp, None]:
    """Deploy openapi connector via Helm, yield a DeployedApp, then clean up.

    All configuration comes from helm/values.yaml. No E2E_* env vars required.
    """
    cmd = [
        "helm",
        "upgrade",
        "--install",
        "--wait",
        f"--timeout={_DEPLOY_TIMEOUT}s",
        "--namespace",
        _NAMESPACE,
        "--create-namespace",
        "-f",
        str(_VALUES_FILE),
        _APP_NAME,
        str(_CHART_PATH),
    ]

    logger.info("helm upgrade --install %s ...", _APP_NAME)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"helm deploy failed (exit {proc.returncode}):\n"
            f"{stderr_bytes.decode(errors='replace')}"
        )
    logger.info("%s deployed successfully.", _APP_NAME)

    app = DeployedApp(namespace=_NAMESPACE, app_name=_APP_NAME)

    try:
        yield app
    finally:
        # Collect logs
        log_dir = Path("test-logs/e2e") / _APP_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        for cmd_log, fname in [
            (
                ["kubectl", "get", "pods", "-n", _NAMESPACE, "-o", "wide"],
                "pods-wide.txt",
            ),
            (
                [
                    "kubectl",
                    "get",
                    "events",
                    "-n",
                    _NAMESPACE,
                    "--sort-by=.lastTimestamp",
                ],
                "events.txt",
            ),
        ]:
            lproc = await asyncio.create_subprocess_exec(
                *cmd_log,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await lproc.communicate()
            if out:
                (log_dir / fname).write_bytes(out)
        logger.info("Logs collected to %s/", log_dir)

        # Uninstall
        for cleanup_cmd in [
            ["helm", "uninstall", _APP_NAME, "--namespace", _NAMESPACE],
            ["kubectl", "delete", "namespace", _NAMESPACE, "--ignore-not-found"],
        ]:
            cp = await asyncio.create_subprocess_exec(
                *cleanup_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await cp.communicate()
