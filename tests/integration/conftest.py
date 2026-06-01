"""Fixtures for integration tests.

Tests run entirely in-process: Temporal starts as an embedded dev server via
the SDK's ``embedded_runtime()``, and secret/state/storage infrastructure is
mocked — no external services required.

Environment variables:
    OPENAPI_AUTH_HEADER: Optional auth header for private spec endpoints.

Run tests with: uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import orjson
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from application_sdk.dev import embedded_runtime
from application_sdk.execution._temporal.backend import TemporalExecutorBackend
from application_sdk.execution._temporal.converter import create_data_converter_for_app
from application_sdk.execution._temporal.worker import create_worker
from application_sdk.infrastructure.context import (
    InfrastructureContext,
    set_infrastructure,
)
from application_sdk.observability.observability import AtlanObservability
from application_sdk.storage import create_local_store, create_memory_store
from application_sdk.testing.mocks import MockSecretStore, MockStateStore
from temporalio.client import Client

# Trigger OpenAPIConnector app registration before create_worker is called.
from app.connector import OpenAPIConnector  # noqa: F401

# Pre-wire a memory store as the deployment objectstore so the periodic
# observability flush does not keep retrying and spamming warnings in tests.
AtlanObservability._deployment_store = create_memory_store()

_TASK_QUEUE = "openapi-queue"


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
        secrets["openapi"] = orjson.dumps(
            {"type": "openapi", "auth_header": openapi_auth_header}
        ).decode()

    ctx = InfrastructureContext(
        state_store=MockStateStore(),
        secret_store=MockSecretStore(secrets),
        storage=create_local_store(store_root),
    )
    set_infrastructure(ctx)
    return ctx


# ---------------------------------------------------------------------------
# Embedded Temporal runtime
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def embedded_temporal():
    """Boot an in-process Temporal dev server for the test session."""
    async with embedded_runtime(log_level="error") as rt:
        yield rt


# ---------------------------------------------------------------------------
# Temporal client and in-process worker fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def temporal_client(embedded_temporal) -> Client:
    """Connect to the embedded Temporal dev server."""
    data_converter = create_data_converter_for_app(OpenAPIConnector)
    return await Client.connect(embedded_temporal.host, data_converter=data_converter)


@pytest_asyncio.fixture(scope="session")
async def openapi_worker(
    temporal_client: Client,
    infrastructure: InfrastructureContext,  # noqa: ARG001 — ensures infra is wired first
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


# ---------------------------------------------------------------------------
# Large-payload fixture — heavy-file round-trip tests
# ---------------------------------------------------------------------------
# Default 100 MiB; override with OPENAPI_LARGE_TEST_SIZE_MIB for stress runs.

_LARGE_PAYLOAD_DEFAULT_MIB = 100
_LARGE_PAYLOAD_SIZE_BYTES = (
    int(os.environ.get("OPENAPI_LARGE_TEST_SIZE_MIB", _LARGE_PAYLOAD_DEFAULT_MIB))
    * 1024
    * 1024
)


def sha256_of_path(path: Path) -> str:
    """Stream-compute the SHA-256 of a file."""
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture(scope="module")
def large_payload_file(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, str, int]:
    """Generate a ≥100 MiB random-bytes file once per test module.

    Returns ``(path, sha256, size_bytes)``.
    """
    import hashlib
    import secrets

    target_dir = tmp_path_factory.mktemp("openapi-large-payload")
    path = target_dir / "payload.bin"

    block = secrets.token_bytes(8 * 1024 * 1024)
    h = hashlib.sha256()
    written = 0
    with path.open("wb") as f:
        while written < _LARGE_PAYLOAD_SIZE_BYTES:
            remaining = _LARGE_PAYLOAD_SIZE_BYTES - written
            buf = block if remaining >= len(block) else block[:remaining]
            f.write(buf)
            h.update(buf)
            written += len(buf)
    return path, h.hexdigest(), written


# ---------------------------------------------------------------------------
# Large valid OpenAPI spec fixture — workflow tests at scale
# ---------------------------------------------------------------------------
# A valid OpenAPI 3.x JSON document the connector can actually parse, sized
# to ≥100 MiB. Used by the large-spec workflow tests; the byte-level tests
# stay on ``large_payload_file`` (random bytes, uncompressible).


_PATH_OP_TAIL = (
    b'","summary":"Retrieve resource by id with extended description and '
    b"parameters that pad each entry so the total document reaches the "
    b'configured target size.","description":"Long-form description used '
    b"as filler so the OpenAPI document is syntactically interesting at "
    b"large sizes. Real customer specs commonly carry similarly verbose "
    b'narrative attached to each operation, so this is representative.",'
    b'"parameters":[{"name":"id","in":"path","required":true,"schema":'
    b'{"type":"string"}},{"name":"verbose","in":"query","schema":'
    b'{"type":"boolean","default":false}}],"responses":{"200":'
    b'{"description":"OK"},"404":{"description":"Not Found"}}}}'
)
_PATH_PREFIX = b'"/resource/'
_PATH_OP_HEAD = b'":{"get":{"operationId":"getResource'


def _write_large_openapi_spec(path: Path, target_bytes: int) -> tuple[str, int, int]:
    """Stream-write a valid OpenAPI 3.x JSON spec of at least *target_bytes*.

    Returns ``(sha256_hex, total_size, num_paths)``. Each path entry is a
    full ``GET`` operation with parameters and responses so the document
    parses cleanly through the connector's ``_extract_spec_async``.
    """
    import hashlib

    h = hashlib.sha256()
    written = 0
    num_paths = 0

    def _emit(f, buf: bytes) -> int:
        h.update(buf)
        f.write(buf)
        return len(buf)

    with path.open("wb") as f:
        header = (
            b'{"openapi":"3.0.4",'
            b'"info":{"title":"OpenAPI Large Test Spec",'
            b'"version":"1.0.0",'
            b'"description":"Synthetic spec generated for chunking / scale tests."},'
            b'"paths":{'
        )
        written += _emit(f, header)

        idx = 0
        while written < target_bytes:
            if idx > 0:
                written += _emit(f, b",")
            id_bytes = str(idx).encode("ascii")
            written += _emit(f, _PATH_PREFIX)
            written += _emit(f, id_bytes)
            written += _emit(f, _PATH_OP_HEAD)
            written += _emit(f, id_bytes)
            written += _emit(f, _PATH_OP_TAIL)
            num_paths += 1
            idx += 1

        written += _emit(f, b"}}")

    return h.hexdigest(), written, num_paths


@pytest.fixture(scope="module")
def large_spec_file(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, str, int, int]:
    """Generate a valid ≥100 MiB OpenAPI JSON spec once per test module.

    Returns ``(path, sha256, size_bytes, num_paths)``. Honors
    ``OPENAPI_LARGE_TEST_SIZE_MIB`` for stress runs.
    """
    target_dir = tmp_path_factory.mktemp("openapi-large-spec")
    path = target_dir / "spec.json"
    digest, size, num_paths = _write_large_openapi_spec(path, _LARGE_PAYLOAD_SIZE_BYTES)
    return path, digest, size, num_paths
