"""Fixtures for integration tests.

Tests connect to an external Temporal dev server.
Secret/state/storage infrastructure is mocked — no Dapr required.

Environment variables:
    TEMPORAL_HOST: Temporal server address (default: ``localhost:7233``).
    OPENAPI_AUTH_HEADER: Optional auth header for private spec endpoints.

Run tests with: uv run pytest tests/integration/ -v
Requires: temporal server start-dev
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from application_sdk.execution._temporal.backend import TemporalExecutorBackend
from application_sdk.execution._temporal.converter import create_data_converter_for_app
from application_sdk.execution._temporal.worker import create_worker
from application_sdk.infrastructure.context import (
    InfrastructureContext,
    set_infrastructure,
)
from application_sdk.storage import create_local_store
from application_sdk.testing.mocks import MockSecretStore, MockStateStore
from temporalio.client import Client

# Trigger OpenAPIConnector app registration before create_worker is called.
from app.connector import OpenAPIConnector  # noqa: F401

_TASK_QUEUE = "openapi-queue"
_TEMPORAL_HOST = os.environ.get("TEMPORAL_HOST", "localhost:7233")
_temporal_reachable: bool | None = None


class AppExecutor:
    """Compatibility shim wrapping TemporalExecutorBackend for integration tests."""

    def __init__(self, backend: TemporalExecutorBackend) -> None:
        self._backend = backend

    async def execute_app(
        self,
        app_cls: Any,
        input_data: Any,
        *,
        execution_id_prefix: str = "",
    ) -> Any:
        from application_sdk.app.context import AppContext
        from application_sdk.execution.retry import RetryPolicy

        app_name = getattr(app_cls, "_app_name", execution_id_prefix or "app")
        context = AppContext(
            app_name=app_name,
            app_version="0.0.0",
            run_id=execution_id_prefix or app_name,
        )
        return await self._backend.execute(
            app_cls,
            input_data,
            context=context,
            retry_policy=RetryPolicy(),
        )


# ---------------------------------------------------------------------------
# Infrastructure fixture — wires mock secret/state/storage (no Dapr)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def store_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Root directory for the session-scoped LocalStore.

    RETAINED-tier files survive here after cleanup_storage runs, because
    cleanup_storage skips RETAINED refs.  Tests can resolve a durable
    FileReference to a local path via ``store_root / ref.storage_path``.
    """
    return tmp_path_factory.mktemp("sdk-store")


@pytest.fixture(scope="session")
def infrastructure(store_root: Path) -> InfrastructureContext:
    """Wire mock infrastructure for the session using a LocalStore."""
    openapi_auth_header = os.environ.get("OPENAPI_AUTH_HEADER", "")
    secrets: dict[str, str] = {}
    if openapi_auth_header:
        secrets["openapi"] = json.dumps(
            {"type": "openapi", "auth_header": openapi_auth_header}
        )

    ctx = InfrastructureContext(
        state_store=MockStateStore(),
        secret_store=MockSecretStore(secrets),
        storage=create_local_store(store_root),
    )
    set_infrastructure(ctx)
    return ctx


# ---------------------------------------------------------------------------
# Temporal connectivity check + graceful skip
# ---------------------------------------------------------------------------


def _check_temporal_reachable(host: str) -> bool:
    """Return True if the Temporal server at *host* responds within 3 seconds."""

    async def _probe() -> bool:
        try:
            client = await Client.connect(host, lazy=True)
            handle = client.get_workflow_handle("__connectivity_probe__")
            await asyncio.wait_for(handle.describe(), timeout=3.0)
            return True  # describe() succeeded unexpectedly
        except asyncio.TimeoutError:
            return False
        except Exception:
            return True  # Any non-timeout error means the server IS reachable

    return asyncio.run(_probe())


@pytest.fixture(autouse=True, scope="session")
def require_temporal() -> None:
    """Skip the entire test session if Temporal is not reachable."""
    global _temporal_reachable
    if _temporal_reachable is None:
        _temporal_reachable = _check_temporal_reachable(_TEMPORAL_HOST)
    if not _temporal_reachable:
        pytest.skip(
            f"Temporal server not running at {_TEMPORAL_HOST} — "
            "start it with: temporal server start-dev"
        )


# ---------------------------------------------------------------------------
# Temporal client and in-process worker fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def temporal_client() -> Client:
    """Connect to the external Temporal dev server."""
    data_converter = create_data_converter_for_app(OpenAPIConnector)
    return await Client.connect(_TEMPORAL_HOST, data_converter=data_converter)


@pytest_asyncio.fixture(scope="session")
async def openapi_worker(
    temporal_client: Client,
    infrastructure: InfrastructureContext,  # noqa: ARG001 — ensures infra is set first
) -> Any:
    """Start the OpenAPI connector worker in-process."""
    w = create_worker(temporal_client, task_queue=_TASK_QUEUE)
    async with w:
        yield


@pytest.fixture(scope="session")
def openapi_executor(
    temporal_client: Client,
    openapi_worker: Any,  # noqa: ARG001 — ensures worker is running
) -> AppExecutor:
    """Executor for OpenAPI connector integration tests."""
    backend = TemporalExecutorBackend(
        client=temporal_client,
        task_queue=_TASK_QUEUE,
    )
    return AppExecutor(backend=backend)
