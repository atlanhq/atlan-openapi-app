"""Fixtures for integration tests.

Tests run entirely in-process: Temporal starts as an embedded dev server via
the SDK's shared integration fixture kit
(``application_sdk.testing.integration.fixtures``), and secret/state/storage
infrastructure is mocked — no external services required. This conftest
star-imports that kit and overrides only what the OpenAPI connector needs;
see ``docs/guides/integration-fixtures.md`` in application-sdk for the full
override contract.

Environment variables:
    OPENAPI_AUTH_HEADER: Optional auth header for private spec endpoints.

Run tests with: uv run pytest tests/integration/ -v
"""

from __future__ import annotations

import os

os.environ.setdefault("ATLAN_APPLICATION_NAME", "openapi")
os.environ.setdefault("ATLAN_DEPLOYMENT_NAME", "ci")

from pathlib import Path

import orjson
import pytest
from application_sdk.testing.integration.fixtures import *  # noqa: F403
from application_sdk.testing.integration.fixtures import AppExecutor

from app.connector import OpenAPIConnector


@pytest.fixture(scope="session")
def integration_app_cls() -> type[OpenAPIConnector]:
    return OpenAPIConnector


@pytest.fixture(scope="session")
def integration_source() -> str:
    """The OpenAPI auth header, read straight from the environment.

    Not a container or an HTTP fake — the connector under test consumes a
    spec URL passed inline in each test's workflow input, so there is nothing
    for this fixture to bring up. It exists only to hand the header through
    to ``integration_secrets``.
    """
    return os.environ.get("OPENAPI_AUTH_HEADER", "")


@pytest.fixture(scope="session")
def integration_secrets(integration_source: str) -> dict[str, str]:
    if not integration_source:
        return {}
    return {
        "openapi": orjson.dumps(
            {"type": "openapi", "auth_header": integration_source}
        ).decode()
    }


@pytest.fixture(scope="session")
def openapi_executor(executor: AppExecutor) -> AppExecutor:
    return executor


# Target size for the large-spec fixture below (≥100 MiB; override for stress runs).
_LARGE_PAYLOAD_DEFAULT_MIB = 100
_LARGE_PAYLOAD_SIZE_BYTES = (
    int(os.environ.get("OPENAPI_LARGE_TEST_SIZE_MIB", _LARGE_PAYLOAD_DEFAULT_MIB))
    * 1024
    * 1024
)


# ---------------------------------------------------------------------------
# Large valid OpenAPI spec fixture — workflow tests at scale
# ---------------------------------------------------------------------------
# A valid OpenAPI 3.x JSON document the connector can actually parse, sized
# to ≥100 MiB. Used by the large-spec workflow tests. (Byte-level large-payload
# round-trips moved to application-sdk's storage emulator suite.)


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
