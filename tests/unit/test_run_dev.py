"""Unit tests for the local dev server entrypoint (app/run_dev.py).

``_build_object_store_credential`` is pure, env-var-driven logic. ``main()``
is exercised by stubbing the ``run_dev_combined`` seam so the module's dev
wiring (env parsing, secret/credential assembly) runs for real without
starting an actual Temporal dev server.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import orjson
import pytest

from app.run_dev import _build_object_store_credential, main


# ---------------------------------------------------------------------------
# _build_object_store_credential
# ---------------------------------------------------------------------------


class TestBuildObjectStoreCredential:
    def test_s3_default_auth_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAPI_SOURCE_AUTH_TYPE", raising=False)
        monkeypatch.setenv("OPENAPI_S3_BUCKET", "my-bucket")
        monkeypatch.setenv("OPENAPI_S3_REGION", "us-west-2")
        monkeypatch.setenv("OPENAPI_AWS_ROLE_ARN", "arn:aws:iam::123:role/x")
        monkeypatch.setenv("OPENAPI_SOURCE_USERNAME", "AKIA")
        monkeypatch.setenv("OPENAPI_SOURCE_PASSWORD", "secret")

        cred = _build_object_store_credential()

        assert cred["authType"] == "s3"
        assert cred["username"] == "AKIA"
        assert cred["password"] == "secret"
        assert cred["extra"]["s3_bucket"] == "my-bucket"
        assert cred["extra"]["region"] == "us-west-2"
        assert cred["extra"]["aws_role_arn"] == "arn:aws:iam::123:role/x"

    def test_gcs_auth_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAPI_SOURCE_AUTH_TYPE", "gcs")
        monkeypatch.setenv("OPENAPI_GCS_BUCKET", "my-gcs-bucket")

        cred = _build_object_store_credential()

        assert cred["authType"] == "gcs"
        assert cred["extra"] == {"gcs_bucket": "my-gcs-bucket"}

    def test_adls_auth_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAPI_SOURCE_AUTH_TYPE", "adls")
        monkeypatch.setenv("OPENAPI_AZURE_STORAGE_ACCOUNT", "myaccount")
        monkeypatch.setenv("OPENAPI_ADLS_CONTAINER", "mycontainer")
        monkeypatch.setenv("OPENAPI_AZURE_TENANT_ID", "tenant-123")

        cred = _build_object_store_credential()

        assert cred["authType"] == "adls"
        assert cred["extra"] == {
            "storage_account_name": "myaccount",
            "adls_container": "mycontainer",
            "azure_tenant_id": "tenant-123",
        }

    def test_unknown_auth_type_leaves_extra_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAPI_SOURCE_AUTH_TYPE", "unrecognised")

        cred = _build_object_store_credential()

        assert cred["authType"] == "unrecognised"
        assert cred["extra"] == {}


# ---------------------------------------------------------------------------
# main() — env parsing + credential/secret wiring, run_dev_combined stubbed
# ---------------------------------------------------------------------------


class TestMain:
    async def test_url_import_type_without_atlan_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ATLAN_API_KEY", raising=False)
        monkeypatch.setenv("OPENAPI_IMPORT_TYPE", "URL")
        monkeypatch.setenv(
            "OPENAPI_SPEC_URL", "https://petstore3.swagger.io/api/v3/openapi.json"
        )
        run_dev_mock = AsyncMock()
        monkeypatch.setattr("app.run_dev.run_dev_combined", run_dev_mock)

        await main()

        run_dev_mock.assert_awaited_once()
        _, kwargs = run_dev_mock.call_args
        example_input = kwargs["example_input"]
        assert example_input["import_type"] == "URL"
        assert "openapi_credential" not in example_input
        credential_stores = kwargs["credential_stores"]
        assert "atlan" not in credential_stores["default"]._secrets

    async def test_cloud_import_type_with_atlan_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ATLAN_API_KEY", "atl-test-token")
        monkeypatch.setenv("ATLAN_BASE_URL", "https://example.atlan.com")
        monkeypatch.setenv("OPENAPI_IMPORT_TYPE", "CLOUD")
        monkeypatch.setenv("OPENAPI_SPEC_PREFIX", "specs")
        monkeypatch.setenv("OPENAPI_SPEC_KEY", "openapi.json")
        monkeypatch.setenv("OPENAPI_SOURCE_AUTH_TYPE", "s3")
        run_dev_mock = AsyncMock()
        monkeypatch.setattr("app.run_dev.run_dev_combined", run_dev_mock)

        await main()

        run_dev_mock.assert_awaited_once()
        _, kwargs = run_dev_mock.call_args
        example_input = kwargs["example_input"]
        assert example_input["import_type"] == "CLOUD"
        assert example_input["openapi_credential"] == {
            "name": "openapi_source",
            "credential_type": "openapi",
        }
        credential_stores = kwargs["credential_stores"]
        secrets = credential_stores["default"]._secrets
        atlan_secret = orjson.loads(secrets["atlan"])
        assert atlan_secret["token"] == "atl-test-token"
        assert "openapi_source" in secrets
