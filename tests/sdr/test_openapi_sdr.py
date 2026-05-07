"""SDR integration tests for the OpenAPI Spec Loader connector.

Validates the connector running inside a customer-style SDR container
(built by atlan-configurator + docker compose) rather than the local
Dapr + Temporal + direct-Python stack used by tests/integration/.

The SDR container exposes the SDK FastAPI server on localhost:8000.
Tests submit a workflow via POST /workflows/v1/start and poll
GET /workflows/v1/status/{wf}/{run} until COMPLETED.

Uses the public Swagger Petstore spec — no connector credentials required.

Prerequisites
-------------
The SDR container must already be running on ``localhost:8000``.
The CI workflow handles this automatically.

For local runs, build and start the stack first:

    APP_IMAGE=ghcr.io/atlanhq/atlan-openapi-app:main-<sha> \\
        docker compose \\
            -f ci-deploy/docker-compose.yaml \\
            -f .github/e2e/docker-compose.ci.yml \\
            up -d

Env vars (all optional — public Petstore needs none):
    OPENAPI_SPEC_URL    Override the target spec URL (default: Petstore v3)
    ATLAN_APPLICATION_NAME  Set to "openapi" (done by CI workflow automatically)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar, Dict

from application_sdk.testing.integration import (
    Scenario,
    equals,
    is_not_empty,
)
from application_sdk.testing.sdr import BaseSDRIntegrationTest

_TESTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TESTS_DIR.parent

try:
    from dotenv import load_dotenv

    for _env_path in (_REPO_ROOT / ".env", _TESTS_DIR / ".env"):
        if _env_path.exists():
            load_dotenv(_env_path, override=False)
except ImportError:
    pass


_SPEC_URL = os.environ.get(
    "OPENAPI_SPEC_URL",
    "https://petstore3.swagger.io/api/v3/openapi.json",
)


class TestOpenAPISdr(BaseSDRIntegrationTest):
    """OpenAPI Spec Loader SDR integration suite.

    Single scenario: full URL-based extraction of the Swagger Petstore spec.
    Uses a public spec — no connector credentials required, so
    ``agent_spec_template`` is empty (no SDR credential resolution chain).

    BaseSDRIntegrationTest._execute_scenario polls for workflow COMPLETED
    whenever workflow_timeout > 0 and no expected_data is set.
    """

    agent_spec_template: ClassVar[Dict[str, Any]] = {}
    timeout: int = 60

    default_credentials: Dict[str, Any] = {}
    default_metadata: Dict[str, Any] = {}
    default_connection: Dict[str, Any] = {
        "typeName": "Connection",
        "attributes": {
            "qualifiedName": "default/api/sdr_test",
            "name": "test_openapi_sdr",
        },
    }

    def _build_scenario_args(self, scenario: Scenario) -> Dict[str, Any]:
        args = super()._build_scenario_args(scenario)
        # AppInputContract.model_validate receives the full request body.
        # Pydantic maps spec_url, import_type, load_to_atlan at the top
        # level — not nested under a "metadata" key — so flatten
        # scenario.metadata into top-level args before dispatch.
        metadata = args.pop("metadata", {})
        args.update(metadata)
        return args

    scenarios = [
        Scenario(
            name="petstore_url_extraction",
            api="workflow",
            metadata={
                "spec_url": _SPEC_URL,
                "import_type": "URL",
                "load_to_atlan": False,
            },
            assert_that={
                "success": equals(True),
                "data.workflow_id": is_not_empty(),
                "data.run_id": is_not_empty(),
            },
            workflow_timeout=600,
            polling_interval=10,
            description=(
                "Full SDR workflow: extract Petstore v3 OpenAPI spec to COMPLETED "
                "on tenant Temporal. Validates the extract_spec → transform pipeline "
                "inside a customer-style atlan-configurator SDR container."
            ),
        ),
    ]
