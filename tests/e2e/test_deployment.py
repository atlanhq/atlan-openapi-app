"""K8s e2e deployment tests for the OpenAPI connector app.

Tests in this file require a running Kubernetes cluster with the app deployed.
All tests depend on the ``deployed_app`` session fixture from conftest.py which
runs ``helm upgrade --install`` and waits for pods to be ready.

Test coverage:
  1. Handler health endpoint responds 200.
  2. All pods in the namespace are Running and Ready.
  3. KEDA ScaledObject exists and reports Ready.

For a full end-to-end workflow test (connector -> Atlan load), use the
standalone script instead::

    python -m tests.e2e.openapi_test [--skip-deploy] [--skip-undeploy] [--image-tag TAG]
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import time

import httpx
import pytest

from tests.e2e.conftest import DeployedApp

from application_sdk.observability.logger_adaptor import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Port-forward helper
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Port {port} did not become ready within {timeout}s")


async def _get(
    namespace: str, service: str, svc_port: int, path: str
) -> httpx.Response:
    """Single GET via an ephemeral kubectl port-forward."""
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            "-n",
            namespace,
            f"svc/{service}",
            f"{port}:{svc_port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await asyncio.to_thread(_wait_for_port, port)
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.get(f"http://127.0.0.1:{port}{path}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Handler health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handler_health(deployed_app: DeployedApp) -> None:
    """GET /health on the handler service returns HTTP 200."""
    response = await _get(
        namespace=deployed_app.namespace,
        service=f"{deployed_app.app_name}-handler",
        svc_port=deployed_app.handler_port,
        path="/health",
    )
    assert response.status_code == 200, (
        f"Handler /health returned {response.status_code}: {response.text}"
    )
    logger.info("Handler /health: %s", response.json())


# ---------------------------------------------------------------------------
# Pod readiness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_pods_ready(deployed_app: DeployedApp) -> None:
    """All pods in the namespace are Running and Ready."""
    proc = await asyncio.create_subprocess_exec(
        "kubectl",
        "get",
        "pods",
        "-n",
        deployed_app.namespace,
        "-o",
        "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    assert proc.returncode == 0, f"kubectl get pods failed: {stderr_bytes.decode()}"

    data = json.loads(stdout_bytes)
    items = data.get("items", [])
    assert items, f"No pods found in namespace '{deployed_app.namespace}'"

    not_ready = []
    for pod in items:
        name = pod.get("metadata", {}).get("name", "?")
        phase = pod.get("status", {}).get("phase", "")
        conditions = pod.get("status", {}).get("conditions", [])
        ready_cond = next((c for c in conditions if c.get("type") == "Ready"), None)
        if phase != "Running" or not ready_cond or ready_cond.get("status") != "True":
            not_ready.append(f"{name} (phase={phase})")

    assert not not_ready, (
        f"Pods not ready in namespace '{deployed_app.namespace}': {not_ready}"
    )


# ---------------------------------------------------------------------------
# KEDA ScaledObject
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keda_scaled_object_exists(deployed_app: DeployedApp) -> None:
    """KEDA ScaledObject for the worker is present and reports Ready.

    Checks that the ScaledObject's ``Ready`` condition is True, meaning KEDA
    can read the Temporal task-queue metric and manage the worker replica count.
    """
    namespace = deployed_app.namespace
    target_name = f"{deployed_app.app_name}-worker"

    proc = await asyncio.create_subprocess_exec(
        "kubectl",
        "get",
        "scaledobject",
        "-n",
        namespace,
        "-o",
        "json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    assert proc.returncode == 0, (
        f"kubectl get scaledobject failed: {stderr_bytes.decode()}"
    )

    data = json.loads(stdout_bytes)
    items = data.get("items", [])
    assert items, f"No ScaledObjects found in namespace '{namespace}'"

    scaled_obj = next(
        (i for i in items if i.get("metadata", {}).get("name") == target_name),
        None,
    )
    assert scaled_obj is not None, (
        f"ScaledObject '{target_name}' not found; found: "
        + ", ".join(i.get("metadata", {}).get("name", "?") for i in items)
    )

    conditions = scaled_obj.get("status", {}).get("conditions", [])
    ready = next((c for c in conditions if c.get("type") == "Ready"), None)
    assert ready is not None, (
        f"ScaledObject '{target_name}' has no Ready condition; status: "
        + json.dumps(scaled_obj.get("status", {}), indent=2)
    )
    assert ready.get("status") == "True", (
        f"ScaledObject '{target_name}' Ready={ready.get('status')}: "
        + ready.get("message", "")
    )
    logger.info("ScaledObject '%s' is Ready", target_name)
