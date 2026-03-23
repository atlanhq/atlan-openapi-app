"""Shared infrastructure for Kubernetes-based end-to-end tests.

Provides reusable components for deploying apps to Kubernetes, running
workflows via the HTTP handler, and validating results. Each connector
test (snowflake, powerbi, pipeline) imports from this module and adds
only connector-specific logic.

Uses ephemeral port-forwards: each HTTP call to a k8s service gets its
own short-lived ``kubectl port-forward`` that is torn down immediately
after the response is received.  This avoids flaky failures from
long-lived port-forwards dying due to idle TCP timeouts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import msgspec

# Repo root: tests/e2e/infra.py -> e2e -> tests -> repo_root
REPO_ROOT = Path(__file__).parent.parent.parent
# Local chart (sibling repo) — override with HELM_CHART_REF env var to use a
# remote OCI ref (e.g. "oci://ghcr.io/atlanhq/charts/atlan-app").
_LOCAL_CHART = str(REPO_ROOT.parent / "application-sdk" / "helm" / "atlan-app")
HELM_CHART = os.environ.get("HELM_CHART_REF", _LOCAL_CHART)
# Only set when using a remote/versioned chart; leave empty for local directory charts.
HELM_CHART_VERSION = os.environ.get("HELM_CHART_VERSION", "")


@dataclass
class AppConfig:
    """Configuration for a deployed app."""

    name: str
    module: str
    namespace: str
    task_queue: str
    values_file: Path | None = None
    """Path to a Helm values override file.

    - Connector apps: set to ``REPO_ROOT / "helm" / "values.yaml"`` (values.yaml at repo root).
    - Shared apps (loader, delete, custom-typedefs): leave ``None`` — appName/appModule
      are injected via ``--set`` by ``deploy_app()``.
    """


# =============================================================================
# Credential helpers
# =============================================================================


def atlan_credential_dict() -> dict[str, str]:
    """Standard Atlan credential reference for workflow inputs."""
    return {
        "name": "atlan",
        "credential_type": "atlan_api_token",
        "store_name": "default",
    }


# =============================================================================
# Ephemeral port-forward helpers
# =============================================================================


def _allocate_free_port() -> int:
    """Ask the OS for a free TCP port and return it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    """Block until *port* on localhost accepts a TCP connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"Port {port} did not become ready within {timeout}s")


def _kill_proc(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
    """Terminate a subprocess, escalating to SIGKILL if needed."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


async def kube_http_call(
    app: AppConfig,
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 30.0,
    startup_timeout: float = 120.0,
) -> httpx.Response:
    """Make a single HTTP call to a k8s service via an ephemeral port-forward."""
    deadline = time.monotonic() + startup_timeout
    last_error: Exception = RuntimeError("startup_timeout must be > 0")

    while time.monotonic() < deadline:
        port = _allocate_free_port()
        proc = subprocess.Popen(
            [
                "kubectl",
                "port-forward",
                "-n",
                app.namespace,
                f"svc/{app.name}-handler",
                f"{port}:80",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        try:
            await asyncio.to_thread(_wait_for_port, port)

            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.request(
                    method,
                    f"http://127.0.0.1:{port}{path}",
                    json=json_body,
                    params=params,
                )
            return response
        except (RuntimeError, OSError) as exc:
            _kill_proc(proc)
            kubectl_stderr = (
                proc.stderr.read().decode(errors="replace").strip()
                if proc.stderr
                else ""
            )
            detail = f" | kubectl: {kubectl_stderr}" if kubectl_stderr else ""
            last_error = RuntimeError(f"{exc}{detail}")
            remaining = deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(min(2.0, remaining))
        finally:
            _kill_proc(proc)

    raise RuntimeError(
        f"Service {app.name}-handler not reachable after {startup_timeout}s"
        f" (last error: {last_error})"
    )


# =============================================================================
# Deployer
# =============================================================================


async def deploy_app(
    app: AppConfig,
    image_tag: str = "latest",
    extra_helm_sets: list[str] | None = None,
    memory_limit: str = "2Gi",
) -> None:
    """Deploy a single app to Kubernetes via Helm (upgrade --install)."""
    print(f"\nDeploying {app.name} (tag={image_tag})...")

    helm_cmd = [
        "helm",
        "upgrade",
        "--install",
        "--wait",
        "--timeout=300s",
        "--namespace",
        app.namespace,
        "--create-namespace",
    ]

    if app.values_file is not None:
        helm_cmd.extend(["-f", str(app.values_file)])
        helm_cmd.extend(["--set-string", f"image.tag={image_tag}"])
    else:
        helm_cmd.extend(["--set", f"appName={app.name}"])
        helm_cmd.extend(["--set", f"appModule={app.module}"])

    helm_cmd.extend(
        [
            "--set",
            "worker.resources.requests.memory=512Mi",
            "--set",
            f"worker.resources.limits.memory={memory_limit}",
        ]
    )

    for item in extra_helm_sets or []:
        helm_cmd.extend(["--set", item])

    helm_cmd.append(app.name)
    helm_cmd.append(str(HELM_CHART))
    if HELM_CHART_VERSION:
        helm_cmd.extend(["--version", HELM_CHART_VERSION])

    proc = await asyncio.create_subprocess_exec(
        *helm_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"Deploy of {app.name} failed (exit {proc.returncode}):\n"
            f"{stderr_bytes.decode(errors='replace')}"
        )
    print(f"  {app.name} deployed successfully")


async def undeploy_app(app: AppConfig) -> None:
    """Uninstall a Helm release and delete its namespace."""
    print(f"\nUndeploying {app.name}...")
    for cmd in [
        ["helm", "uninstall", app.name, "--namespace", app.namespace],
        ["kubectl", "delete", "namespace", app.namespace, "--ignore-not-found"],
    ]:
        cp = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await cp.communicate()
    print(f"  {app.name} undeployed")


# =============================================================================
# Pod exec helpers
# =============================================================================


def get_worker_pod_name(app: AppConfig) -> str:
    """Find worker pod by label selector."""
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            app.namespace,
            "-l",
            f"app={app.name},component=worker",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"Failed to get {app.name} worker pod: {result.stderr}")

    return result.stdout.strip()


def exec_in_pod(
    app: AppConfig,
    command: list[str],
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a command inside an app's worker pod."""
    pod_name = get_worker_pod_name(app)
    cmd = ["kubectl", "exec", "-n", app.namespace, pod_name, "--", *command]

    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def cleanup_in_cluster(app: AppConfig, paths: list[str]) -> None:
    """Remove files/directories in an app's cluster."""
    for path in paths:
        exec_in_pod(app, ["rm", "-rf", path])


# =============================================================================
# Workflow execution helpers
# =============================================================================


def _to_json_dict(obj: Any) -> dict[str, Any]:
    """Serialize a contract Input dataclass (or plain dict) to a JSON-compatible dict.

    Uses msgspec so that pyatlan Connection objects and other non-trivial
    dataclass fields are encoded correctly — the same way Temporal's
    DataConverter would encode them.
    """
    if isinstance(obj, dict):
        return obj
    return json.loads(msgspec.json.encode(obj))


async def run_workflow(
    app: AppConfig,
    input_data: Any,
    *,
    poll_interval: float = 5.0,
    timeout_minutes: float = 30.0,
) -> tuple[dict[str, Any], str, str]:
    """POST ``/start``, poll ``/result``, return ``(result, workflow_id, correlation_id)``."""
    response = await kube_http_call(
        app, "POST", "/workflows/v1/start", json_body=_to_json_dict(input_data)
    )
    if response.status_code >= 400:
        print(
            f"    POST /workflows/v1/start returned {response.status_code}: {response.text}"
        )
    response.raise_for_status()

    run_response = response.json()
    workflow_id = run_response["data"]["workflow_id"]
    correlation_id = run_response.get("correlation_id", workflow_id)

    print(f"  Workflow started: {workflow_id}")
    print(f"  Correlation ID:  {correlation_id}")

    deadline = time.monotonic() + timeout_minutes * 60
    while time.monotonic() < deadline:
        result_response = await kube_http_call(
            app, "GET", f"/workflows/v1/result/{workflow_id}", params={"wait": "false"}
        )
        result_response.raise_for_status()
        envelope = result_response.json()
        data = envelope["data"]

        if data["status"] != "running":
            break

        await asyncio.sleep(poll_interval)
    else:
        raise RuntimeError(
            f"Workflow {workflow_id} did not complete within {timeout_minutes} minutes"
        )

    if data.get("error"):
        print(f"    [ERROR] Workflow {workflow_id}: {data.get('error')}")

    return data.get("result", {}), workflow_id, correlation_id


async def get_timing(
    app: AppConfig, workflow_id: str
) -> tuple[float, list[dict[str, Any]]]:
    """Fetch timing breakdown from the handler."""
    try:
        response = await kube_http_call(
            app, "GET", f"/workflows/v1/timing/{workflow_id}"
        )
        if response.status_code == 200:
            data = response.json()
            return (
                data.get("workflow_duration_ms", 0.0),
                data.get("task_timings", []),
            )
    except Exception as e:
        print(f"    Warning: Failed to fetch timing: {e}")

    return 0.0, []


# =============================================================================
# Local workflow execution (no k8s, no port-forward)
# =============================================================================


async def run_workflow_local(
    base_url: str,
    input_data: Any,
    *,
    poll_interval: float = 5.0,
    timeout_minutes: float = 30.0,
) -> tuple[dict[str, Any], str, str]:
    """POST ``/start``, poll ``/result``, return ``(result, workflow_id, correlation_id)``."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None)) as client:
        response = await client.post(
            f"{base_url}/workflows/v1/start", json=_to_json_dict(input_data)
        )
        if response.status_code >= 400:
            print(
                f"    POST /workflows/v1/start returned {response.status_code}: {response.text}"
            )
        response.raise_for_status()

        run_response = response.json()
        workflow_id = run_response["data"]["workflow_id"]
        correlation_id = run_response.get("correlation_id", workflow_id)

        print(f"  Workflow started: {workflow_id}")
        print(f"  Correlation ID:  {correlation_id}")

        deadline = time.monotonic() + timeout_minutes * 60
        while time.monotonic() < deadline:
            result_response = await client.get(
                f"{base_url}/workflows/v1/result/{workflow_id}",
                params={"wait": "true"},
            )
            result_response.raise_for_status()
            envelope = result_response.json()
            data = envelope["data"]

            if data["status"] != "running":
                break

            await asyncio.sleep(poll_interval)
        else:
            raise RuntimeError(
                f"Workflow {workflow_id} did not complete within {timeout_minutes} minutes"
            )

    if data.get("error"):
        print(f"    [ERROR] Workflow {workflow_id}: {data.get('error')}")

    return data.get("result", {}), workflow_id, correlation_id


# =============================================================================
# Validation helpers
# =============================================================================


def validate_asset_creation(
    expected: int,
    created: int,
    updated: int,
    *,
    min_success_rate: float = 90.0,
) -> tuple[bool, float]:
    """Validate that enough assets were created/updated."""
    actual = created + updated
    if expected > 0:
        rate = (actual / expected) * 100
    else:
        rate = 0.0
    passed = rate >= min_success_rate
    return passed, rate


@dataclass
class AssetTypeSpec:
    """Specification for verifying a particular asset type in Atlan."""

    type_name: str
    min_count: int = 1


@dataclass
class AtlanVerificationResult:
    """Result of verifying assets in Atlan after a connector run."""

    passed: bool = False
    type_counts: dict[str, int] = field(default_factory=dict)
    unexpected_asset_count: int = 0
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def verify_atlan_assets(
    connection_qn: str,
    expected_types: list[AssetTypeSpec],
    *,
    base_url: str | None = None,
    api_key: str | None = None,
) -> AtlanVerificationResult:
    """Query Atlan directly to verify expected asset types exist in a connection."""
    from msgspec import UNSET
    from pyatlan_v9.client.aio.atlan import AsyncAtlanClient
    from pyatlan_v9.model.assets import Asset, Referenceable
    from pyatlan_v9.model.fluent_search import FluentSearch

    if base_url is None:
        base_url = os.environ.get("ATLAN_BASE_URL", "")
    if api_key is None:
        api_key = os.environ.get("ATLAN_API_KEY", "")

    result = AtlanVerificationResult()
    failures: list[str] = []
    warnings: list[str] = []

    try:
        async with AsyncAtlanClient(base_url=base_url, api_key=api_key) as client:
            for spec in expected_types:
                search = (
                    FluentSearch.select()
                    .where(Referenceable.TYPE_NAME.eq(spec.type_name))
                    .where(Asset.CONNECTION_QUALIFIED_NAME.eq(connection_qn))
                    .page_size(3)
                    .include_on_results(Asset.NAME)
                    .include_on_results(Referenceable.QUALIFIED_NAME)
                    .include_on_results(Asset.CONNECTION_QUALIFIED_NAME)
                )
                response = await search.execute_async(client)
                count = response.count
                result.type_counts[spec.type_name] = count

                if count < spec.min_count:
                    failures.append(
                        f"{spec.type_name}: expected at least {spec.min_count}, "
                        f"found {count} in connection '{connection_qn}'"
                    )
                else:
                    for asset in response.current_page()[:2]:
                        if asset.name is UNSET or not asset.name:
                            failures.append(
                                f"{spec.type_name}: sample asset has empty name"
                            )
                        conn_qn_val = asset.connection_qualified_name
                        if conn_qn_val is not UNSET and conn_qn_val != connection_qn:
                            failures.append(
                                f"{spec.type_name}: asset '{asset.name}' has "
                                f"connection_qualified_name='{conn_qn_val}', "
                                f"expected '{connection_qn}'"
                            )

            plain_search = (
                FluentSearch.select()
                .where(Referenceable.TYPE_NAME.eq("Asset"))
                .where(Asset.CONNECTION_QUALIFIED_NAME.eq(connection_qn))
                .page_size(0)
            )
            plain_response = await plain_search.execute_async(client)
            result.unexpected_asset_count = plain_response.count
            if result.unexpected_asset_count > 0:
                warnings.append(
                    f"Found {result.unexpected_asset_count} plain 'Asset' (untyped) "
                    f"assets in connection — possible type-registration bug"
                )

    except Exception as e:
        failures.append(f"Atlan verification error: {e}")

    result.failures = failures
    result.warnings = warnings
    result.passed = len(failures) == 0
    return result


# =============================================================================
# Formatting helpers
# =============================================================================


def format_duration(ms: float) -> str:
    """Format milliseconds to a human-readable duration."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = secs / 60
    return f"{mins:.1f}m"


# =============================================================================
# Log collection
# =============================================================================


@dataclass
class WorkflowLogRef:
    """Tracks a single workflow run for log collection."""

    app: AppConfig
    correlation_id: str
    label: str


class LogCollector:
    """Collects logs from Kubernetes pods after an E2E test run."""

    def __init__(
        self,
        test_name: str,
        apps: list[AppConfig],
        log_dir: Path | None = None,
    ) -> None:
        self._test_name = test_name
        self._apps = apps
        self._refs: list[WorkflowLogRef] = []
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base_dir = log_dir if log_dir is not None else Path("e2e-logs")
        self._output_dir = base_dir / f"{test_name}-{timestamp}"

    def record(self, app: AppConfig, correlation_id: str, label: str) -> None:
        """Record a workflow run for later structured log collection."""
        if correlation_id:
            self._refs.append(
                WorkflowLogRef(app=app, correlation_id=correlation_id, label=label)
            )

    async def collect_all(self) -> None:
        """Collect all logs. Best-effort — never raises."""
        try:
            await self._collect()
        except Exception as e:
            print(f"  [log-collect] WARNING: Log collection failed: {e}")

    async def _collect(self) -> None:
        """Internal collection implementation."""
        self._output_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n[log-collect] Collecting logs to: {self._output_dir}")

        kubectl_tasks = []
        for app in self._apps:
            kubectl_dir = self._output_dir / "kubectl" / app.name
            kubectl_dir.mkdir(parents=True, exist_ok=True)
            kubectl_tasks.append(
                asyncio.to_thread(self._collect_kubectl_logs, app, kubectl_dir)
            )
        await asyncio.gather(*kubectl_tasks, return_exceptions=True)

        if self._refs:
            structured_dir = self._output_dir / "structured"
            structured_dir.mkdir(parents=True, exist_ok=True)
            for ref in self._refs:
                await self._collect_structured_logs(ref, structured_dir)

        self._write_summary()

    def _collect_kubectl_logs(self, app: AppConfig, kubectl_dir: Path) -> None:
        """Collect kubectl logs for all pods in an app's namespace (blocking)."""
        try:
            pods_result = subprocess.run(
                [
                    "kubectl",
                    "get",
                    "pods",
                    "-n",
                    app.namespace,
                    "-o",
                    "jsonpath={.items[*].metadata.name}",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if pods_result.returncode != 0 or not pods_result.stdout.strip():
                return

            pods = pods_result.stdout.strip().split()
            for pod in pods:
                try:
                    containers_result = subprocess.run(
                        [
                            "kubectl",
                            "get",
                            "pod",
                            pod,
                            "-n",
                            app.namespace,
                            "-o",
                            "jsonpath={.spec.containers[*].name}",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if containers_result.returncode != 0:
                        continue
                    containers = containers_result.stdout.strip().split()
                except Exception:
                    continue

                for container in containers:
                    try:
                        logs = subprocess.run(
                            [
                                "kubectl",
                                "logs",
                                "-n",
                                app.namespace,
                                pod,
                                "-c",
                                container,
                                "--tail=10000",
                            ],
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        if logs.returncode == 0 and logs.stdout:
                            (kubectl_dir / f"{container}-{pod}.log").write_text(
                                logs.stdout
                            )
                    except Exception:
                        pass

        except Exception as e:
            print(f"  [log-collect] WARNING: kubectl logs for {app.name}: {e}")

    async def _collect_structured_logs(
        self, ref: WorkflowLogRef, structured_dir: Path
    ) -> None:
        """Download structured OTEL logs for a workflow run via handler API."""
        try:
            corr_prefix = ref.correlation_id[:8]
            filename = f"{ref.label}-{corr_prefix}.jsonl"
            response = await kube_http_call(
                ref.app,
                "GET",
                f"/workflows/v1/logs/correlation/{ref.correlation_id}/download",
                params={"format": "json", "include_children": "true"},
                timeout=60.0,
            )
            if response.status_code == 200 and response.text:
                (structured_dir / filename).write_text(response.text)
        except Exception as e:
            print(f"  [log-collect] WARNING: structured logs {ref.label}: {e}")

    def _write_summary(self) -> None:
        """Write summary.txt listing all correlation IDs and collected files."""
        try:
            lines = [
                f"Log collection summary for: {self._test_name}",
                f"Output directory: {self._output_dir}",
                "",
                "Workflow runs recorded:",
            ]
            for ref in self._refs:
                lines.append(
                    f"  {ref.label}: correlation_id={ref.correlation_id} (app={ref.app.name})"
                )
            lines.append("")
            lines.append("Files collected:")
            for f in sorted(self._output_dir.rglob("*")):
                if f.is_file() and f.name != "summary.txt":
                    rel = f.relative_to(self._output_dir)
                    lines.append(f"  {rel}")
            (self._output_dir / "summary.txt").write_text("\n".join(lines) + "\n")
        except Exception as e:
            print(f"  [log-collect] WARNING: summary write failed: {e}")


# =============================================================================
# CLI helpers
# =============================================================================


def create_base_argument_parser(description: str) -> argparse.ArgumentParser:
    """Create an ``ArgumentParser`` with flags shared across all E2E tests."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument("--skip-undeploy", action="store_true")
    parser.add_argument("--image-tag", type=str, default="latest")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--log-dir", type=str, default=None)
    return parser


def validate_env_vars(required: list[str]) -> list[str]:
    """Return list of missing environment variables."""
    return [v for v in required if not os.environ.get(v)]


def output_result(text: str, path: str | None) -> None:
    """Write ``text`` to *path* or stdout."""
    if path:
        Path(path).write_text(text)
        print(f"\nResults written to {path}")
    else:
        print(text)
