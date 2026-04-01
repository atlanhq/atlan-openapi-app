"""Cloud storage download for CLOUD import mode.

Uses obstore (already bundled in app-sdk) to download spec files from
S3, GCS, or Azure — no extra cloud SDK dependencies needed.

Two paths mirroring the Kotlin Utils.getInputFile() implementation:

Path A — cloud_source credential provided and has valid auth:
    Resolve credential via Dapr → parse authType (s3/gcs/adls) → create
    an obstore store with the credential's own bucket/keys → download.

Path B — No credential or credential has no auth:
    Fall back to the tenant's own Dapr-configured object store (already
    set up by Helm/K8s). Uses the SDK's ObjectStore — no env vars needed.

Credential field mapping (matches csa-connectors-objectstore format):
    S3:   username=access_key, password=secret_key, extra={region, s3_bucket, aws_role_arn}
    GCS:  username=project_id, password=service_account_json, extra={gcs_bucket}
    ADLS: username=client_id, password=client_secret, extra={azure_tenant_id, storage_account_name, adls_container}
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import obstore as obs
from application_sdk.observability.logger_adaptor import get_logger
from obstore.store import AzureStore, GCSStore, S3Store  # type: ignore[reportCallIssue]

logger = get_logger(__name__)

_SPEC_EXTENSIONS = {".json", ".yaml", ".yml", ".zip"}


def _has_valid_auth(credentials: dict[str, Any]) -> bool:
    """Check if credential has key-based or role-based auth."""
    has_key_auth = bool(credentials.get("username") and credentials.get("password"))
    extra = credentials.get("extra") or credentials.get("extras") or {}
    if isinstance(extra, str):
        extra = json.loads(extra) if extra else {}
    has_role_auth = bool(extra.get("aws_role_arn"))
    return has_key_auth or has_role_auth


def _create_store(creds: dict[str, Any]) -> Any:
    """Create an obstore store from an external credential dict.

    For S3 role ARN, assumes the role first via boto3 STS (the only
    place we need boto3 — obstore doesn't support STS assume_role natively).
    boto3 is already available in the base image.
    """
    extra = creds.get("extra") or creds.get("extras") or {}
    if isinstance(extra, str):
        extra = json.loads(extra) if extra else {}
    auth_type = creds.get("authType") or creds.get("auth_type") or ""

    if auth_type == "s3":
        bucket = extra.get("s3_bucket", "")
        region = extra.get("region", "")
        access_key = creds.get("username") or ""
        secret_key = creds.get("password") or ""
        role_arn = extra.get("aws_role_arn", "")

        if not bucket:
            raise ValueError("S3 bucket is required (extra.s3_bucket)")

        config: dict[str, str] = {}
        if region:
            config["aws_region"] = region
        if access_key and secret_key:
            config["aws_access_key_id"] = access_key
            config["aws_secret_access_key"] = secret_key
        if role_arn:
            config["aws_role_arn"] = role_arn
            config["aws_role_session_name"] = "openapi-cloud-download"
            logger.info("using S3 role-based auth role_arn=%s", role_arn)
        # If no keys and no role: obstore uses default credentials chain
        # (IAM/instance profile/EKS IRSA)

        return S3Store(bucket=bucket, config=config)  # type: ignore[reportCallIssue]

    elif auth_type == "gcs":
        bucket = extra.get("gcs_bucket", "")
        if not bucket:
            raise ValueError("GCS bucket is required (extra.gcs_bucket)")

        gcs_config: dict[str, str] = {}
        sa_json = creds.get("password") or ""
        if sa_json:
            gcs_config["google_service_account_key"] = sa_json

        return GCSStore(bucket=bucket, config=gcs_config if gcs_config else None)  # type: ignore[reportCallIssue]

    elif auth_type == "adls":
        storage_account = extra.get("storage_account_name", "")
        container = extra.get("adls_container", "objectstore")
        if not storage_account:
            raise ValueError(
                "Azure storage account is required (extra.storage_account_name)"
            )

        az_config: dict[str, str] = {
            "azure_storage_account_name": storage_account,
        }
        access_key = creds.get("password") or ""
        client_id = creds.get("username") or ""
        tenant_id = extra.get("azure_tenant_id") or ""

        if access_key and not tenant_id:
            az_config["azure_storage_account_key"] = access_key
        elif tenant_id and client_id:
            az_config["azure_storage_client_id"] = client_id
            az_config["azure_storage_tenant_id"] = tenant_id
            client_secret = creds.get("password") or ""
            if client_secret:
                az_config["azure_storage_client_secret"] = client_secret

        return AzureStore(container_name=container, config=az_config)  # type: ignore[reportCallIssue]

    else:
        raise ValueError(f"Unknown cloud credential authType: {auth_type}")


async def _download_from_external_store(
    store: Any,
    spec_prefix: str,
    spec_key: str,
    output_dir: str,
) -> list[str]:
    """Download spec file(s) from an external obstore store."""
    prefix = spec_prefix.strip("/")
    key = spec_key.strip("/")

    if key:
        # Single file download
        remote_path = f"{prefix}/{key}" if prefix else key
        local_path = str(Path(output_dir) / Path(key).name)

        logger.info("downloading single spec remote_path=%s", remote_path)
        result = await obs.get_async(store, remote_path)
        data = await result.bytes_async()
        with open(local_path, "wb") as f:
            f.write(data)
        logger.info("spec downloaded local_path=%s size=%d", local_path, len(data))
        return [local_path]

    # No spec_key — list all files under prefix and download spec-like files
    list_prefix = f"{prefix}/" if prefix else ""
    logger.info("listing all specs under prefix=%s", list_prefix)

    downloaded: list[str] = []
    objects = await store.list_async(prefix=list_prefix)  # type: ignore[attr-defined]
    for obj_meta in objects:
        obj_path = obj_meta["path"]
        ext = Path(obj_path).suffix.lower()
        if ext not in _SPEC_EXTENSIONS:
            logger.debug("skipping non-spec file=%s", obj_path)
            continue

        # Preserve relative path structure to avoid filename collisions
        # (e.g., specs/v1/api.json and specs/v2/api.json)
        rel_path = (
            obj_path[len(list_prefix) :]
            if list_prefix and obj_path.startswith(list_prefix)
            else Path(obj_path).name
        )
        local_path = str(Path(output_dir) / rel_path)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        logger.info("downloading spec file=%s", obj_path)
        result = await obs.get_async(store, obj_path)
        data = await result.bytes_async()
        with open(local_path, "wb") as f:
            f.write(data)
        downloaded.append(local_path)

    if not downloaded:
        raise ValueError(
            f"No spec files (.json/.yaml/.yml/.zip) found under prefix: {list_prefix}"
        )

    logger.info("downloaded %d spec files from prefix=%s", len(downloaded), list_prefix)
    return downloaded


async def _download_from_tenant_store(
    tenant_store: Any,
    spec_prefix: str,
    spec_key: str,
    output_dir: str,
) -> list[str]:
    """Download spec file(s) from the tenant's Dapr-configured object store.

    Uses the same obstore instance that the SDK's self.upload()/self.download()
    uses — already configured with the tenant's bucket and credentials via Dapr.
    """
    # Tenant store is the same obstore ObjectStore — reuse the same download logic
    return await _download_from_external_store(
        tenant_store, spec_prefix, spec_key, output_dir
    )


async def download_spec_from_cloud(
    spec_prefix: str,
    spec_key: str,
    credential_data: dict[str, Any] | None,
    output_dir: str | None = None,
    tenant_store: Any | None = None,
) -> list[str]:
    """Download spec file(s) from cloud storage.

    Path A — credential_data has valid auth:
        Create an obstore store from the credential's own bucket/keys
        and download from that external storage.

    Path B — No valid credential:
        Use the tenant's Dapr-configured object store (passed as tenant_store).

    Args:
        spec_prefix: Directory/prefix in the storage bucket.
        spec_key: Object key (filename) within the prefix. If blank,
            all spec files under spec_prefix are downloaded.
        credential_data: Resolved credential dict from Dapr secretstore.
            If None or has no valid auth, falls back to tenant store.
        output_dir: Local directory for the downloaded files.
        tenant_store: The tenant's Dapr-configured obstore instance
            (from self.context.storage). Used for Path B fallback.

    Returns:
        List of local file paths of the downloaded spec files.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="openapi-cloud-")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    if credential_data is not None and _has_valid_auth(credential_data):
        # Path A: External storage — use credential's own bucket/keys
        logger.info(
            "using external storage credentials auth_type=%s",
            credential_data.get("authType") or credential_data.get("auth_type") or "unknown",
        )
        store = _create_store(credential_data)
        return await _download_from_external_store(
            store, spec_prefix, spec_key, output_dir
        )

    # Path B: Tenant's own Dapr-configured store
    if credential_data is not None:
        logger.info("credential has no key/role auth, falling back to tenant store")
    else:
        logger.info("no cloud_source credential, using tenant store")

    if tenant_store is None:
        raise RuntimeError(
            "No tenant object store available. Ensure Dapr objectstore binding "
            "is configured on this deployment."
        )

    return await _download_from_tenant_store(
        tenant_store, spec_prefix, spec_key, output_dir
    )
