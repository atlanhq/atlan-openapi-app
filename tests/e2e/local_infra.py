"""Shared infrastructure for local dry-run regression tests.

Provides a process manager that starts app workers and handlers as local
subprocesses against a local Temporal server.
No Kubernetes or Helm required.

All processes are wrapped with ``dapr run`` so that DAPR secret stores are
available, matching the production deployment model.

Usage:
    from tests.e2e.local_infra import LocalProcessManager, OPENAPI_LOCAL

    tmp_dir = prepare_dapr_components({})
    mgr = LocalProcessManager(os.path.join(tmp_dir, "components"))
    mgr.start_app(OPENAPI_LOCAL)                 # connector: worker + handler
    ...
    mgr.stop_all()
    shutil.rmtree(tmp_dir, ignore_errors=True)

Prerequisites:
    - ``dapr`` CLI installed (``brew install dapr/tap/dapr-cli`` or dapr install script)
    - ``dapr init --slim`` (no Docker required)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


def _free_port() -> int:
    """Ask the OS for a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class LocalAppConfig:
    """Configuration for a locally-running app."""

    name: str
    module: str
    task_queue: str
    handler_port: int
    worker_health_port: int


# ---------------------------------------------------------------------------
# Shared app configurations (every connector test uses these)
# ---------------------------------------------------------------------------

CUSTOM_TYPEDEFS_LOCAL = LocalAppConfig(
    name="custom-typedefs",
    module="custom_typedefs.custom_typedefs:CustomTypedefs",
    task_queue="custom-typedefs-queue",
    handler_port=0,  # No handler needed — called as child workflow
    worker_health_port=9093,
)

# ---------------------------------------------------------------------------
# Connector-specific LocalAppConfig definitions
# ---------------------------------------------------------------------------

OPENAPI_LOCAL = LocalAppConfig(
    name="openapi",
    module="app.connector:OpenAPIConnector",
    task_queue="openapi-queue",
    handler_port=9080,
    worker_health_port=9091,
)


# ---------------------------------------------------------------------------
# DAPR component preparation
# ---------------------------------------------------------------------------


def prepare_dapr_components(secrets: dict[str, str]) -> str:
    """Create a temp directory with DAPR component configs and a secrets file.

    Args:
        secrets: Dictionary of secret name -> JSON string value.

    Returns:
        Path to the temporary directory (contains ``components/`` subdirectory).
        Caller is responsible for cleanup via ``shutil.rmtree()``.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="dapr-local-e2e-"))
    components_dir = tmp_dir / "components"
    components_dir.mkdir(parents=True)

    secrets_file = tmp_dir / "secrets.json"
    with secrets_file.open("w") as f:
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
    with (components_dir / "secretstore.yaml").open("w") as f:
        f.write(secretstore_yaml)

    objectstore_dir = tmp_dir / "objectstore"
    objectstore_dir.mkdir(parents=True)
    objectstore_yaml = f"""apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: objectstore
spec:
  type: bindings.localstorage
  version: v1
  ignoreErrors: true
  metadata:
    - name: rootPath
      value: {objectstore_dir}
"""
    with (components_dir / "objectstore.yaml").open("w") as f:
        f.write(objectstore_yaml)

    statestore_dir = tmp_dir / "statestore"
    statestore_dir.mkdir(parents=True)
    statestore_yaml = """apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: statestore
spec:
  type: state.in-memory
  version: v1
"""
    with (components_dir / "statestore.yaml").open("w") as f:
        f.write(statestore_yaml)

    eventstore_yaml = """apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: eventstore
spec:
  type: bindings.localstorage
  version: v1
  metadata:
    - name: rootPath
      value: /tmp/dapr-eventstore
"""
    with (components_dir / "eventstore.yaml").open("w") as f:
        f.write(eventstore_yaml)

    return str(tmp_dir)


# ---------------------------------------------------------------------------
# Process manager
# ---------------------------------------------------------------------------


class LocalProcessManager:
    """Manages local app subprocesses for testing."""

    def __init__(self, dapr_components_path: str) -> None:
        self._procs: dict[str, list[subprocess.Popen[bytes]]] = {}
        self._app_configs: dict[str, LocalAppConfig] = {}
        self._dapr_components_path = dapr_components_path
        self._actual_ports: dict[str, tuple[int, int]] = {}

    def start_app(self, app: LocalAppConfig, *, handler: bool = True) -> None:
        """Start worker (and optionally handler) subprocesses for *app*."""
        procs: list[subprocess.Popen[bytes]] = []
        show_logs = os.environ.get("SHOW_WORKER_LOGS", "") == "1"
        out = None if show_logs else subprocess.DEVNULL

        log_file = Path(tempfile.gettempdir()) / f"app-log-{app.task_queue}.jsonl"
        with contextlib.suppress(FileNotFoundError):
            log_file.unlink()
        proc_env = {
            **os.environ,
            "LOG_FILE_PATH": str(log_file),
            "DAPR_COMPONENTS_PATH": self._dapr_components_path,
        }

        actual_worker_port = _free_port()
        has_handler = handler and app.handler_port > 0
        actual_handler_port = _free_port() if has_handler else 0

        dapr_grpc_port = _free_port()
        dapr_http_port = _free_port()

        worker_cmd = [
            "dapr",
            "run",
            "--app-id",
            f"local-{app.task_queue}",
            "--app-port",
            str(actual_worker_port),
            "--dapr-http-port",
            str(dapr_http_port),
            "--dapr-grpc-port",
            str(dapr_grpc_port),
            "--resources-path",
            self._dapr_components_path,
            "--log-level",
            "warn",
            "--placement-host-address",
            "",
            "--scheduler-host-address",
            "",
            "--",
            sys.executable,
            "-m",
            "application_sdk.main",
            "--mode",
            "worker",
            "--app",
            app.module,
            "--task-queue",
            app.task_queue,
            "--health-port",
            str(actual_worker_port),
        ]
        procs.append(
            subprocess.Popen(
                worker_cmd,
                stdout=out,
                stderr=out,
                env=proc_env,
                start_new_session=True,
            )
        )

        if has_handler:
            handler_dapr_grpc = _free_port()
            handler_dapr_http = _free_port()

            handler_cmd = [
                "dapr",
                "run",
                "--app-id",
                f"local-handler-{app.task_queue}",
                "--app-port",
                str(actual_handler_port),
                "--dapr-http-port",
                str(handler_dapr_http),
                "--dapr-grpc-port",
                str(handler_dapr_grpc),
                "--resources-path",
                self._dapr_components_path,
                "--log-level",
                "warn",
                "--placement-host-address",
                "",
                "--scheduler-host-address",
                "",
                "--",
                sys.executable,
                "-m",
                "application_sdk.main",
                "--mode",
                "handler",
                "--app",
                app.module,
                "--task-queue",
                app.task_queue,
                "--handler-port",
                str(actual_handler_port),
            ]
            procs.append(
                subprocess.Popen(
                    handler_cmd,
                    stdout=out,
                    stderr=out,
                    env=proc_env,
                    start_new_session=True,
                )
            )

        self._procs[app.name] = procs
        self._app_configs[app.name] = app
        self._actual_ports[app.name] = (actual_worker_port, actual_handler_port)

        if has_handler:
            if not self.wait_for_ready(actual_handler_port, timeout=45):
                raise RuntimeError(
                    f"{app.name} handler did not become ready on port {actual_handler_port} within 45s"
                )
            if not self.wait_for_ready(actual_worker_port, timeout=45):
                raise RuntimeError(
                    f"{app.name} worker did not become ready on port {actual_worker_port} within 45s"
                )
        else:
            if not self.wait_for_ready(actual_worker_port, timeout=45):
                raise RuntimeError(
                    f"{app.name} did not become ready on port {actual_worker_port} within 45s"
                )
        print(f"  {app.name} ready (worker:{actual_worker_port})")

    def stop_app(self, app_name: str) -> None:
        """Stop all processes for *app_name*."""
        procs = self._procs.pop(app_name, [])
        self._app_configs.pop(app_name, None)
        self._actual_ports.pop(app_name, None)
        for proc in procs:
            self._terminate(proc)

    def stop_all(self) -> None:
        """Stop every tracked process."""
        for name in list(self._procs):
            self.stop_app(name)

    def get_handler_url(self, app_name: str) -> str:
        """Return the base URL for the handler of *app_name*."""
        _, handler_port = self._actual_ports[app_name]
        if handler_port == 0:
            raise ValueError(f"{app_name} was started without a handler")
        return f"http://localhost:{handler_port}"

    @staticmethod
    def wait_for_ready(port: int, timeout: int = 45) -> bool:
        """Poll ``GET /health`` on *port* until it responds or *timeout* expires."""
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

    @staticmethod
    def _terminate(proc: subprocess.Popen[bytes]) -> None:
        """Kill the entire process group, then wait."""
        import os as _os

        try:
            pgid = _os.getpgid(proc.pid)
            _os.killpg(pgid, signal.SIGTERM)
            proc.wait(timeout=10)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            try:
                pgid = _os.getpgid(proc.pid)
                _os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=5)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI helpers (local-only, no K8s flags)
# ---------------------------------------------------------------------------


def create_local_argument_parser(
    description: str,
    available_tests: list[str] | None = None,
) -> argparse.ArgumentParser:
    """Argument parser for local regression tests."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", type=str, default=None)
    if available_tests:
        parser.add_argument(
            "--tests",
            type=str,
            default=None,
            help=(
                f"Comma-separated list of tests to run (default: all). "
                f"Available: {', '.join(available_tests)}"
            ),
        )
    return parser
