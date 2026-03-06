"""Fixtures for integration tests.

Each test only starts the workers it needs via fixture dependencies.
Workers are started as session-scoped fixtures and terminated when the session ends.

Workers are wrapped with `dapr run` so that DAPR secret stores are available,
matching the production deployment model.

Run tests with: make test-integration
Requires:
    - temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true
    - dapr CLI installed

Environment variables:
    SHOW_WORKER_LOGS=1: Show worker logs in real-time
    OTEL_EXPORTER_OTLP_ENDPOINT: Export traces/metrics/logs to collector
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Generator

import pytest
import pytest_asyncio
from temporalio.client import Client

from app_framework.execution._temporal.backend import TemporalExecutorBackend
from app_framework.execution.executor import AppExecutor


def _free_port() -> int:
    """Ask the OS for a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_ready(port: int, timeout: int = 45) -> bool:
    """Poll GET /health on *port* until it responds or *timeout* expires."""
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"http://localhost:{port}/health")
                if resp.status_code < 500:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _prepare_dapr_components(secrets: dict[str, str]) -> str:
    """Create a temp directory with DAPR component configs and a secrets file."""
    tmp_dir = tempfile.mkdtemp(prefix="dapr-integration-")
    components_dir = os.path.join(tmp_dir, "components")
    os.makedirs(components_dir)

    secrets_file = os.path.join(tmp_dir, "secrets.json")
    with open(secrets_file, "w") as f:
        json.dump(secrets, f)

    secretstore_yaml = f"""apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: secretstore
spec:
  type: secretstores.local.file
  version: v1
  metadata:
    - name: secretsFile
      value: {secrets_file}
    - name: nestedSeparator
      value: ":"
"""
    with open(os.path.join(components_dir, "secretstore.yaml"), "w") as f:
        f.write(secretstore_yaml)

    return tmp_dir


def _start_worker(
    app_module: str,
    task_queue: str,
    dapr_components_path: str,
    *,
    health_port: int | None = None,
) -> subprocess.Popen[bytes]:
    """Start a worker wrapped with `dapr run` for DAPR integration.

    IMPORTANT: Uses DEVNULL, not PIPE — PIPE without reading causes
    deadlock when the OS pipe buffer (~64KB) fills up.
    """
    if health_port is None:
        health_port = _free_port()

    dapr_grpc_port = _free_port()
    dapr_http_port = _free_port()

    worker_env = os.environ.copy()
    show_logs = os.environ.get("SHOW_WORKER_LOGS", "0") == "1"

    dapr_app_id = f"test-{task_queue}"

    cmd = [
        "dapr",
        "run",
        "--app-id",
        dapr_app_id,
        "--app-port",
        str(health_port),
        "--dapr-http-port",
        str(dapr_http_port),
        "--dapr-grpc-port",
        str(dapr_grpc_port),
        "--resources-path",
        dapr_components_path,
        "--log-level",
        "warn",
        "--placement-host-address",
        "",
        "--scheduler-host-address",
        "",
        "--",
        sys.executable,
        "-m",
        "app_framework.main",
        "--mode",
        "worker",
        "--app",
        app_module,
        "--task-queue",
        task_queue,
        "--health-port",
        str(health_port),
    ]

    if show_logs:
        proc = subprocess.Popen(cmd, env=worker_env, start_new_session=True)
    else:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=worker_env,
            start_new_session=True,
        )

    if not _wait_for_ready(health_port, timeout=45):
        raise RuntimeError(
            f"Worker for {task_queue} did not become ready on port {health_port} within 45s"
        )
    return proc


def _stop_worker(proc: subprocess.Popen[bytes]) -> None:
    """Stop a worker subprocess via process group kill."""
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        print("\nWaiting for OTEL metrics to flush...")
        time.sleep(12)

    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=10)
    except ProcessLookupError:
        pass
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)
    except OSError:
        pass


@pytest.fixture(scope="session")
def dapr_components_dir() -> Generator[str, None, None]:
    """Session-scoped DAPR components directory with a local secrets file."""
    secrets: dict[str, str] = {}

    # Atlan API token (for tests that load to Atlan)
    atlan_key = os.environ.get("ATLAN_API_KEY", "")
    if atlan_key:
        secrets["atlan"] = json.dumps(
            {
                "type": "atlan_api_token",
                "token": atlan_key,
                "base_url": os.environ.get("ATLAN_BASE_URL", ""),
            }
        )

    # OpenAPI credential (optional — only needed for private spec endpoints)
    openapi_auth_header = os.environ.get("OPENAPI_AUTH_HEADER", "")
    if openapi_auth_header:
        secrets["openapi"] = json.dumps(
            {
                "type": "openapi",
                "auth_header": openapi_auth_header,
            }
        )

    tmp_dir = _prepare_dapr_components(secrets)
    yield os.path.join(tmp_dir, "components")
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest_asyncio.fixture(scope="session")
async def temporal_client() -> Client:
    """Connect to Temporal cluster at 127.0.0.1:7233."""
    return await Client.connect("127.0.0.1:7233")


@pytest_asyncio.fixture(scope="session")
async def temporal_client_msgspec() -> Client:
    """Connect to Temporal with msgspec data converter for pyatlan Asset serialization."""
    from app_framework.execution._temporal.converter import (
        create_data_converter,
        get_msgspec_payload_converter,
    )

    data_converter = create_data_converter(
        additional_converters=[get_msgspec_payload_converter()]
    )
    return await Client.connect("127.0.0.1:7233", data_converter=data_converter)


@pytest.fixture(scope="session")
def atlan_loader_worker(
    dapr_components_dir: str,
) -> Generator[subprocess.Popen[bytes], None, None]:
    """Start the Atlan loader worker."""
    proc = _start_worker(
        "atlan_loader.loader:AtlanLoader",
        "atlan-loader-queue",
        dapr_components_dir,
    )
    yield proc
    _stop_worker(proc)


@pytest.fixture(scope="session")
def atlan_loader_executor(
    temporal_client_msgspec: Client,
    atlan_loader_worker: subprocess.Popen[bytes],  # noqa: ARG001
) -> AppExecutor:
    """Executor for atlan-loader tests (uses msgspec converter)."""
    backend = TemporalExecutorBackend(
        client=temporal_client_msgspec,
        task_queue="atlan-loader-queue",
    )
    return AppExecutor(backend=backend)


# ---------------------------------------------------------------------------
# OpenAPI connector worker and executor fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def openapi_worker(
    dapr_components_dir: str,
) -> Generator[subprocess.Popen[bytes], None, None]:
    """Start the OpenAPI connector worker."""
    proc = _start_worker(
        "openapi.connector:OpenAPIConnector",
        "openapi-queue",
        dapr_components_dir,
    )
    yield proc
    _stop_worker(proc)


@pytest.fixture(scope="session")
def openapi_executor(
    temporal_client_msgspec: Client,
    openapi_worker: subprocess.Popen[bytes],  # noqa: ARG001
) -> AppExecutor:
    """Executor for OpenAPI connector integration tests."""
    backend = TemporalExecutorBackend(
        client=temporal_client_msgspec,
        task_queue="openapi-queue",
    )
    return AppExecutor(backend=backend)
