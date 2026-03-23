"""Replay integration test fixtures for the OpenAPI connector.

Sets up in-process Temporal workers backed by scraped replay fixtures from
extracts/openapi/replay/. No source credentials or DAPR required.

One worker is started:
  1. connector worker on "openapi-replay-queue"

Requires:
    - Temporal dev server at 127.0.0.1:7233
    - extracts/openapi/replay/ directory from /scrape-source

Run with:
    temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true
    uv run pytest tests/replay/ -v
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
from obstore.store import LocalStore
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from application_sdk.execution._temporal.activities import get_all_task_activities
from application_sdk.execution._temporal.backend import TemporalExecutorBackend
from application_sdk.execution._temporal.converter import create_data_converter
from application_sdk.execution._temporal.workflows import get_all_app_workflows
from application_sdk.infrastructure.context import (
    InfrastructureContext,
    set_infrastructure,
)


class AppExecutor:
    """Compatibility shim wrapping TemporalExecutorBackend for replay tests."""

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
# Import connector module — triggers @app/@task registration
# ---------------------------------------------------------------------------
import app.connector  # noqa: E402, F401

# ---------------------------------------------------------------------------
# Replay extracts directory
# ---------------------------------------------------------------------------
_EXTRACTS_DIR = Path(__file__).parent.parent.parent / "extracts" / "openapi"
_REPLAY_DIR = _EXTRACTS_DIR / "replay"


def _load_spec_url() -> str:
    """Read the spec URL from metadata.json."""
    metadata_path = _EXTRACTS_DIR / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path) as f:
            return json.load(f).get(
                "base_url", "https://petstore3.swagger.io/api/v3/openapi.json"
            )
    return "https://petstore3.swagger.io/api/v3/openapi.json"


def _load_spec_response() -> tuple[dict, dict]:
    """Load raw/spec/response_001.json and raw/spec/headers_001.json."""
    body_path = _EXTRACTS_DIR / "raw" / "spec" / "response_001.json"
    headers_path = _EXTRACTS_DIR / "raw" / "spec" / "headers_001.json"

    if not body_path.exists():
        raise FileNotFoundError(
            f"Spec response not found at {body_path} — run scrape.py first"
        )

    with open(body_path) as f:
        body = json.load(f)

    header_info: dict = {"status_code": 200, "headers": {}}
    if headers_path.exists():
        with open(headers_path) as f:
            header_info = json.load(f)

    return body, header_info


# ---------------------------------------------------------------------------
# Respx mock fixture — class-scoped so it stays active during fixture setup
# and all test methods in each test class.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
def mock_openapi_spec() -> Generator[respx.MockRouter, None, None]:
    """Intercept all httpx calls to the OpenAPI spec URL and return scraped data.

    Class-scoped so the mock is active during class-scoped fixture setup
    (extraction_result, first_run_result) as well as during test methods.
    """
    spec_url = _load_spec_url()
    body, header_info = _load_spec_response()

    response_headers = {
        k: v for k, v in header_info.get("headers", {}).items() if isinstance(v, str)
    }
    status_code = header_info.get("status_code", 200)

    with respx.mock(assert_all_mocked=False) as router:
        router.get(spec_url).mock(
            return_value=httpx.Response(
                status_code,
                json=body,
                headers=response_headers,
            )
        )
        yield router


# ---------------------------------------------------------------------------
# Temporal client and workers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def temporal_client() -> Client:
    """Connect to Temporal dev server at 127.0.0.1:7233.

    Uses the msgspec data converter so pyatlan Asset objects serialise
    correctly when passed through Temporal to the connector worker.
    """
    data_converter = create_data_converter()
    return await Client.connect("127.0.0.1:7233", data_converter=data_converter)


@pytest.fixture(scope="session")
def store_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Root directory for the session-scoped local object store."""
    return tmp_path_factory.mktemp("object_store")


@pytest.fixture(scope="session")
def local_object_store(store_root: Path) -> LocalStore:
    """File-backed object store for the test session.

    Provides a LocalStore rooted in a session-scoped temp directory so that
    FileReference persistence (local_path → storage_path) works end-to-end
    and RETAINED-tier refs survive post-run cleanup.
    """
    store = LocalStore(prefix=store_root, mkdir=True)
    set_infrastructure(InfrastructureContext(storage=store))
    return store


@pytest_asyncio.fixture(scope="session")
async def replay_worker(
    temporal_client: Client,
    local_object_store: LocalStore,  # noqa: ARG001 — ensures infrastructure is set before worker starts
) -> AsyncGenerator[Worker, None]:
    """In-process Temporal worker for the connector.

    Unlike the subprocess-based integration worker, this worker runs in the
    same process as the test — enabling respx intercepts to work inside activities.
    """
    from temporalio import activity as _activity

    workflows = get_all_app_workflows()
    _seen: set[str] = set()
    activities = []
    for act in get_all_task_activities():
        defn = _activity._Definition.from_callable(act)
        name = defn.name if defn else act.__name__
        if name not in _seen:
            _seen.add(name)
            activities.append(act)

    async with Worker(
        temporal_client,
        task_queue="openapi-replay-queue",
        workflows=workflows,
        activities=activities,
        workflow_runner=UnsandboxedWorkflowRunner(),
    ) as connector_worker:
        yield connector_worker


@pytest_asyncio.fixture(scope="session")
async def replay_executor(
    temporal_client: Client,
    replay_worker: Worker,  # noqa: ARG001 — ensures worker is running
) -> AppExecutor:
    """AppExecutor backed by the in-process replay worker."""
    backend = TemporalExecutorBackend(
        client=temporal_client,
        task_queue="openapi-replay-queue",
    )
    return AppExecutor(backend=backend)
