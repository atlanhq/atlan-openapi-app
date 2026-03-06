"""Replay integration test fixtures for the OpenAPI connector.

Sets up in-process Temporal workers backed by scraped replay fixtures from
extracts/openapi/replay/. No source credentials or DAPR required.

Two workers are started:
  1. connector worker on "openapi-replay-queue"
  2. atlan-loader worker on "atlan-loader-queue" (for dry-run validation)

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

import httpx
import pytest
import pytest_asyncio
import respx
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

import temporalio.activity as _ta
from app_framework.execution._temporal.activities import get_all_task_activities
from app_framework.execution._temporal.backend import TemporalExecutorBackend
from app_framework.execution._temporal.converter import create_data_converter
from app_framework.execution._temporal.workflows import get_all_app_workflows
from app_framework.execution.executor import AppExecutor

# ---------------------------------------------------------------------------
# Import connector module — triggers @app/@task registration
# ---------------------------------------------------------------------------
import openapi.connector  # noqa: F401

# ---------------------------------------------------------------------------
# Import atlan-loader module — triggers its @app/@task registration so the
# second in-process worker can serve the atlan-loader-queue.
# ---------------------------------------------------------------------------
import atlan_loader.loader  # noqa: F401

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
            return json.load(f).get("base_url", "https://petstore3.swagger.io/api/v3/openapi.json")
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
        k: v
        for k, v in header_info.get("headers", {}).items()
        if isinstance(v, str)
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
    correctly when passed through Temporal to the atlan-loader worker.
    """
    data_converter = create_data_converter()
    return await Client.connect("127.0.0.1:7233", data_converter=data_converter)


@pytest_asyncio.fixture(scope="session")
async def replay_worker(
    temporal_client: Client,
) -> AsyncGenerator[Worker, None]:
    """In-process Temporal workers for both the connector and atlan-loader.

    Unlike the subprocess-based integration worker, these workers run in the
    same process as the test — enabling respx intercepts to work inside activities.

    Two nested workers:
    - connector worker on "openapi-replay-queue"
    - loader worker on "atlan-loader-queue" (handles dry-run validation calls)
    """
    workflows = get_all_app_workflows()

    # Deduplicate activities by name — both connector and atlan-loader import
    # shared framework activities (commit_checkpoint, merge_files, etc.) which
    # get registered twice when both modules are imported in the same process.
    _seen: set[str] = set()
    activities = []
    for act in get_all_task_activities():
        name = _ta._Definition.must_from_callable(act).name
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
        async with Worker(
            temporal_client,
            task_queue="atlan-loader-queue",
            workflows=workflows,
            activities=activities,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
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
