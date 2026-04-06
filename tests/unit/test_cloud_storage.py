"""Unit tests for cloud_storage module.

Tests credential parsing, auth validation, and the download dispatch logic
(Path A external vs Path B tenant fallback).

obstore store constructors eagerly validate credentials, so _create_store
tests that would hit real cloud APIs are patched. Pure logic tests
(_has_valid_auth, error cases) run without mocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.cloud_storage import (
    _has_valid_auth,
    download_spec_from_cloud,
)


# =============================================================================
# _has_valid_auth
# =============================================================================


class TestHasValidAuth:
    def test_key_auth(self):
        creds = {"username": "AKIA...", "password": "secret", "extra": {}}
        assert _has_valid_auth(creds) is True

    def test_role_auth(self):
        creds = {
            "username": None,
            "password": None,
            "extra": {"aws_role_arn": "arn:aws:iam::123:role/test"},
        }
        assert _has_valid_auth(creds) is True

    def test_no_auth(self):
        creds = {"username": None, "password": None, "extra": {}}
        assert _has_valid_auth(creds) is False

    def test_empty_username(self):
        creds = {"username": "", "password": "secret", "extra": {}}
        assert _has_valid_auth(creds) is False

    def test_extra_as_json_string(self):
        creds = {
            "username": None,
            "password": None,
            "extra": json.dumps({"aws_role_arn": "arn:aws:iam::123:role/x"}),
        }
        assert _has_valid_auth(creds) is True

    def test_extra_empty_string(self):
        creds = {"username": None, "password": None, "extra": ""}
        assert _has_valid_auth(creds) is False


# =============================================================================
# _create_store — validation / error paths (no mock needed)
# =============================================================================


class TestCreateStoreValidation:
    def test_s3_missing_bucket(self):
        from app.cloud_storage import _create_store

        creds = {"authType": "s3", "username": "", "password": "", "extra": {}}
        with pytest.raises(ValueError, match="S3 bucket is required"):
            _create_store(creds)

    def test_gcs_missing_bucket(self):
        from app.cloud_storage import _create_store

        creds = {"authType": "gcs", "username": "", "password": "", "extra": {}}
        with pytest.raises(ValueError, match="GCS bucket is required"):
            _create_store(creds)

    def test_adls_missing_account(self):
        from app.cloud_storage import _create_store

        creds = {"authType": "adls", "username": "", "password": "", "extra": {}}
        with pytest.raises(ValueError, match="Azure storage account is required"):
            _create_store(creds)

    def test_unknown_auth_type(self):
        from app.cloud_storage import _create_store

        creds = {"authType": "ftp", "username": "", "password": "", "extra": {}}
        with pytest.raises(ValueError, match="Unknown cloud credential authType"):
            _create_store(creds)

    def test_extra_as_json_string_parsed(self):
        from app.cloud_storage import _create_store

        creds = {
            "authType": "s3",
            "username": "key",
            "password": "secret",
            "extra": json.dumps({"region": "ap-south-1", "s3_bucket": "json-bucket"}),
        }
        store = _create_store(creds)
        assert store.config["bucket"] == "json-bucket"


# =============================================================================
# _create_store — store construction (mocked to avoid real cloud validation)
# =============================================================================


class TestCreateStoreConstruction:
    @patch("app.cloud_storage.S3Store")
    def test_s3_key_auth(self, mock_s3store):
        from app.cloud_storage import _create_store

        mock_s3store.return_value = MagicMock()
        creds = {
            "authType": "s3",
            "username": "AKIA_TEST",
            "password": "SECRET_TEST",
            "extra": {"region": "us-east-1", "s3_bucket": "my-bucket"},
        }
        _create_store(creds)
        mock_s3store.assert_called_once()
        call_kwargs = mock_s3store.call_args
        assert call_kwargs.kwargs["bucket"] == "my-bucket"
        config = call_kwargs.kwargs["config"]
        assert config["aws_access_key_id"] == "AKIA_TEST"
        assert config["aws_secret_access_key"] == "SECRET_TEST"
        assert config["aws_region"] == "us-east-1"

    @patch("app.cloud_storage.S3Store")
    def test_s3_role_arn(self, mock_s3store):
        from app.cloud_storage import _create_store

        mock_s3store.return_value = MagicMock()
        creds = {
            "authType": "s3",
            "username": "AKIA",
            "password": "SECRET",
            "extra": {
                "region": "eu-west-1",
                "s3_bucket": "role-bucket",
                "aws_role_arn": "arn:aws:iam::123456:role/MyRole",
            },
        }
        _create_store(creds)
        config = mock_s3store.call_args.kwargs["config"]
        assert config["aws_role_arn"] == "arn:aws:iam::123456:role/MyRole"
        assert config["aws_role_session_name"] == "openapi-cloud-download"

    @patch("app.cloud_storage.S3Store")
    def test_s3_default_creds(self, mock_s3store):
        """No keys, no role — default credentials chain."""
        from app.cloud_storage import _create_store

        mock_s3store.return_value = MagicMock()
        creds = {
            "authType": "s3",
            "username": None,
            "password": None,
            "extra": {"region": "us-west-2", "s3_bucket": "default-bucket"},
        }
        _create_store(creds)
        config = mock_s3store.call_args.kwargs["config"]
        assert "aws_access_key_id" not in config
        assert "aws_role_arn" not in config

    @patch("app.cloud_storage.GCSStore")
    def test_gcs_sa_json(self, mock_gcsstore):
        from app.cloud_storage import _create_store

        mock_gcsstore.return_value = MagicMock()
        creds = {
            "authType": "gcs",
            "username": "my-project",
            "password": '{"type": "service_account", "private_key": "xxx"}',
            "extra": {"gcs_bucket": "gcs-bucket"},
        }
        _create_store(creds)
        config = mock_gcsstore.call_args.kwargs["config"]
        assert "google_service_account_key" in config

    @patch("app.cloud_storage.AzureStore")
    def test_adls_access_key(self, mock_azstore):
        from app.cloud_storage import _create_store

        mock_azstore.return_value = MagicMock()
        creds = {
            "authType": "adls",
            "username": None,
            "password": "storage-key-123",
            "extra": {
                "storage_account_name": "mystorageaccount",
                "adls_container": "mycontainer",
            },
        }
        _create_store(creds)
        config = mock_azstore.call_args.kwargs["config"]
        assert config["azure_storage_account_key"] == "storage-key-123"

    @patch("app.cloud_storage.AzureStore")
    def test_adls_service_principal(self, mock_azstore):
        from app.cloud_storage import _create_store

        mock_azstore.return_value = MagicMock()
        creds = {
            "authType": "adls",
            "username": "client-id-123",
            "password": "client-secret-456",
            "extra": {
                "azure_tenant_id": "tenant-id-789",
                "storage_account_name": "spstorageaccount",
                "adls_container": "spcontainer",
            },
        }
        _create_store(creds)
        config = mock_azstore.call_args.kwargs["config"]
        assert config["azure_storage_client_id"] == "client-id-123"
        assert config["azure_storage_tenant_id"] == "tenant-id-789"


# =============================================================================
# download_spec_from_cloud — dispatch logic
# =============================================================================


class TestDownloadSpecFromCloud:
    @pytest.fixture
    def spec_data(self):
        return b'{"openapi": "3.0.0", "info": {"title": "Test"}, "paths": {}}'

    @pytest.fixture
    def mock_store(self, spec_data):
        """Create a mock obstore store with get_async."""
        store = MagicMock()
        return store

    async def test_path_a_single_file(self, tmp_path, spec_data):
        """Path A: credential with valid auth → external store, single file."""
        creds = {
            "authType": "s3",
            "username": "key",
            "password": "secret",
            "extra": {"region": "us-east-1", "s3_bucket": "ext-bucket"},
        }

        mock_result = AsyncMock()
        mock_result.bytes_async = AsyncMock(return_value=spec_data)

        with (
            patch("app.cloud_storage._create_store") as mock_create,
            patch("app.cloud_storage.obs") as mock_obs,
        ):
            mock_create.return_value = MagicMock()
            mock_obs.get_async = AsyncMock(return_value=mock_result)

            result = await download_spec_from_cloud(
                spec_prefix="specs",
                spec_key="api.json",
                credential_data=creds,
                output_dir=str(tmp_path),
            )

        assert len(result) == 1
        assert result[0].endswith("api.json")
        assert Path(result[0]).exists()
        assert Path(result[0]).read_bytes() == spec_data

    async def test_path_b_tenant_store(self, tmp_path, spec_data):
        """Path B: no credential → tenant store."""
        mock_result = AsyncMock()
        mock_result.bytes_async = AsyncMock(return_value=spec_data)
        tenant_store = MagicMock()

        with patch("app.cloud_storage.obs") as mock_obs:
            mock_obs.get_async = AsyncMock(return_value=mock_result)

            result = await download_spec_from_cloud(
                spec_prefix="specs",
                spec_key="api.json",
                credential_data=None,
                output_dir=str(tmp_path),
                tenant_store=tenant_store,
            )

        assert len(result) == 1
        # Should have been called with the tenant_store
        mock_obs.get_async.assert_called_once()
        call_args = mock_obs.get_async.call_args
        assert call_args[0][0] is tenant_store

    async def test_path_b_no_tenant_store_raises(self):
        """Path B with no tenant store configured → RuntimeError."""
        with pytest.raises(RuntimeError, match="No tenant object store"):
            await download_spec_from_cloud(
                spec_prefix="specs",
                spec_key="api.json",
                credential_data=None,
                tenant_store=None,
            )

    async def test_path_b_credential_no_auth_falls_back(self, tmp_path, spec_data):
        """Credential exists but has no valid auth → Path B fallback."""
        creds = {
            "authType": "s3",
            "username": None,
            "password": None,
            "extra": {"s3_bucket": "some-bucket"},
        }
        tenant_store = MagicMock()
        mock_result = AsyncMock()
        mock_result.bytes_async = AsyncMock(return_value=spec_data)

        with patch("app.cloud_storage.obs") as mock_obs:
            mock_obs.get_async = AsyncMock(return_value=mock_result)

            result = await download_spec_from_cloud(
                spec_prefix="specs",
                spec_key="api.json",
                credential_data=creds,
                output_dir=str(tmp_path),
                tenant_store=tenant_store,
            )

        assert len(result) == 1
        # Should fall back to tenant_store
        call_args = mock_obs.get_async.call_args
        assert call_args[0][0] is tenant_store

    async def test_prefix_listing(self, tmp_path, spec_data):
        """No spec_key → list prefix and download all spec files."""
        creds = {
            "authType": "s3",
            "username": "key",
            "password": "secret",
            "extra": {"region": "us-east-1", "s3_bucket": "ext-bucket"},
        }
        mock_result = AsyncMock()
        mock_result.bytes_async = AsyncMock(return_value=spec_data)

        with (
            patch("app.cloud_storage._create_store") as mock_create,
            patch("app.cloud_storage.obs") as mock_obs,
        ):
            mock_create.return_value = MagicMock()
            mock_create.return_value.list_async = AsyncMock(
                return_value=[
                    {"path": "specs/api1.json", "size": 100},
                    {"path": "specs/api2.yaml", "size": 200},
                    {"path": "specs/readme.txt", "size": 50},
                ]
            )
            mock_obs.get_async = AsyncMock(return_value=mock_result)

            result = await download_spec_from_cloud(
                spec_prefix="specs",
                spec_key="",
                credential_data=creds,
                output_dir=str(tmp_path),
            )

        assert len(result) == 2
        filenames = [Path(p).name for p in result]
        assert "api1.json" in filenames
        assert "api2.yaml" in filenames
        assert "readme.txt" not in filenames

    async def test_prefix_listing_no_specs_found(self, tmp_path):
        """Prefix listing with no spec files → ValueError."""
        creds = {
            "authType": "s3",
            "username": "key",
            "password": "secret",
            "extra": {"region": "us-east-1", "s3_bucket": "ext-bucket"},
        }

        with patch("app.cloud_storage._create_store") as mock_create:
            mock_create.return_value = MagicMock()
            mock_create.return_value.list_async = AsyncMock(
                return_value=[{"path": "specs/readme.txt", "size": 50}]
            )

            with pytest.raises(ValueError, match="No spec files"):
                await download_spec_from_cloud(
                    spec_prefix="specs",
                    spec_key="",
                    credential_data=creds,
                    output_dir=str(tmp_path),
                )

    async def test_single_file_correct_remote_path(self, tmp_path, spec_data):
        """Verify remote path is correctly constructed from prefix + key."""
        creds = {
            "authType": "s3",
            "username": "key",
            "password": "secret",
            "extra": {"region": "us-east-1", "s3_bucket": "b"},
        }
        mock_result = AsyncMock()
        mock_result.bytes_async = AsyncMock(return_value=spec_data)

        with (
            patch("app.cloud_storage._create_store") as mock_create,
            patch("app.cloud_storage.obs") as mock_obs,
        ):
            mock_create.return_value = MagicMock()
            mock_obs.get_async = AsyncMock(return_value=mock_result)

            await download_spec_from_cloud(
                spec_prefix="path/to/specs",
                spec_key="openapi.json",
                credential_data=creds,
                output_dir=str(tmp_path),
            )

        call_args = mock_obs.get_async.call_args
        assert call_args[0][1] == "path/to/specs/openapi.json"

    async def test_key_only_no_prefix(self, tmp_path, spec_data):
        """spec_key without prefix → remote_path is just the key."""
        creds = {
            "authType": "s3",
            "username": "key",
            "password": "secret",
            "extra": {"region": "us-east-1", "s3_bucket": "b"},
        }
        mock_result = AsyncMock()
        mock_result.bytes_async = AsyncMock(return_value=spec_data)

        with (
            patch("app.cloud_storage._create_store") as mock_create,
            patch("app.cloud_storage.obs") as mock_obs,
        ):
            mock_create.return_value = MagicMock()
            mock_obs.get_async = AsyncMock(return_value=mock_result)

            await download_spec_from_cloud(
                spec_prefix="",
                spec_key="openapi.json",
                credential_data=creds,
                output_dir=str(tmp_path),
            )

        call_args = mock_obs.get_async.call_args
        assert call_args[0][1] == "openapi.json"
