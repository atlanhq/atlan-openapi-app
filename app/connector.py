"""OpenAPI Spec Loader Connector App.

Extracts metadata from an OpenAPI v3 spec document (APISpec + APIPath) and
transforms it to JSONL format compatible with publish-app.

The connector:
1. Fetches the OpenAPI spec document via a single HTTP GET
2. Parses the spec into APISpec (1) and APIPath (N) records — bundled in one task
3. Transforms to Atlan asset format using pyatlan built-in types
4. Uploads NDJSON to object storage and calls publish-app (if requested)

Extraction pattern: per-scope-item bundled (indivisible API — one GET returns
both APISpec and APIPath data; splitting would require downloading twice).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, TypeVar, cast

import msgspec
from application_sdk.app import App, task
from application_sdk.contracts.storage import UploadInput
from application_sdk.contracts.types import FileReference, StorageTier
from application_sdk.observability.logger_adaptor import AtlanLoggerAdapter as Logger

from app.api_types import OpenAPIPathRecord, OpenAPISpecRecord
from app.asset_mapper import (
    build_api_spec_qn,
    map_api_path,
    map_api_spec,
    map_connection,
)
from app.contracts import (
    DownloadCloudSpecInput,
    DownloadCloudSpecOutput,
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
    TransformInput,
    TransformOutput,
)
from app.credentials import VALIDATED_AUTH_HEADER_KEY

T = TypeVar("T")

# =============================================================================
# Module-level constants
# =============================================================================


def _enc_hook(obj: Any) -> Any:
    """Handle non-standard types during msgspec JSON encoding."""
    return str(obj)


_encoder = msgspec.json.Encoder(enc_hook=_enc_hook)


def _iter_jsonl(ref: FileReference | None, cls: type[T]) -> "Any":
    """Yield typed objects from a JSONL file using msgspec for decoding."""
    if ref is None:
        return
    path = Path(ref.local_path or "")
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open("rb") as f:
        for line in f:
            line = line.strip()
            if line:
                yield msgspec.json.decode(line, type=cls)


# =============================================================================
# HTTP methods tracked for APIPath operations
# =============================================================================

_TRACKED_METHODS = ("get", "post", "put", "patch", "delete")


# =============================================================================
# Extraction helper
# =============================================================================


async def _extract_spec_async(
    spec_url: str,
    connection_qualified_name: str,
    output_dir: str,
    auth_header: str,
    logger: Logger,
) -> tuple[FileReference, FileReference, int, int]:
    """Fetch the OpenAPI spec and write APISpec + APIPath JSONL files.

    Returns:
        Tuple of (api_spec_file, api_path_file, api_spec_count, api_path_count).
    """
    from app.api_client import OpenAPIApiClient

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = OpenAPIApiClient(auth_header=auth_header)
    try:
        specs = await client.fetch_spec(spec_url)
    finally:
        await client.close()

    spec_file_path = out_dir / "api_spec.jsonl"
    path_file_path = out_dir / "api_path.jsonl"
    spec_count = 0
    path_count = 0

    with spec_file_path.open("wb") as spec_f, path_file_path.open("wb") as path_f:
        for spec in specs:
            info = spec.get("info", {})
            title = info.get("title", "")
            if not title:
                logger.warning("skipping spec with missing 'info.title'")
                continue

            spec_qn = build_api_spec_qn(connection_qualified_name, title)

            # --- Write APISpec record ---
            spec_record = OpenAPISpecRecord(
                title=title,
                openapi_version=spec.get("openapi", spec.get("swagger", "")),
                description=info.get("description", ""),
                terms_of_service=info.get("termsOfService", ""),
                contact_name=info.get("contact", {}).get("name", "")
                if info.get("contact")
                else "",
                contact_email=info.get("contact", {}).get("email", "")
                if info.get("contact")
                else "",
                contact_url=info.get("contact", {}).get("url", "")
                if info.get("contact")
                else "",
                license_name=info.get("license", {}).get("name", "")
                if info.get("license")
                else "",
                license_url=info.get("license", {}).get("url", "")
                if info.get("license")
                else "",
                spec_version=info.get("version", ""),
                external_docs_url=spec.get("externalDocs", {}).get("url", "")
                if spec.get("externalDocs")
                else "",
                external_docs_description=spec.get("externalDocs", {}).get(
                    "description", ""
                )
                if spec.get("externalDocs")
                else "",
                spec_url=spec_url,
            )
            spec_f.write(_encoder.encode(spec_record) + b"\n")
            spec_count += 1

            # --- Write APIPath records ---
            paths = spec.get("paths", {})
            for path_url, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue

                # Collect available operations (uppercase methods) — matches Kotlin's addOperationDetails
                operations: list[str] = [
                    m.upper() for m in _TRACKED_METHODS if path_item.get(m) is not None
                ]

                # Build markdown description table (format matches Kotlin exactly)
                description = ""
                if operations:
                    rows = ["| Method | Summary|", "|---|---|"]
                    for method in _TRACKED_METHODS:
                        op = path_item.get(method)
                        if op is not None:
                            op_summary = (
                                op.get("summary", "") if isinstance(op, dict) else ""
                            )
                            rows.append(f"| `{method.upper()}` |{op_summary} |")
                    description = "\n".join(rows)

                path_record = OpenAPIPathRecord(
                    path_url=path_url,
                    spec_title=title,
                    spec_qualified_name=spec_qn,
                    summary=path_item.get("summary", ""),
                    available_operations=operations,
                    description=description,
                    is_templated="{" in path_url and "}" in path_url,
                )
                path_f.write(_encoder.encode(path_record) + b"\n")
                path_count += 1

    logger.info(
        "spec extracted api_spec_count=%d api_path_count=%d",
        spec_count,
        path_count,
    )
    return (
        FileReference(local_path=str(spec_file_path), tier=StorageTier.RETAINED),
        FileReference(local_path=str(path_file_path), tier=StorageTier.RETAINED),
        spec_count,
        path_count,
    )


# =============================================================================
# Transform helper
# =============================================================================


def _transform_blocking(
    input: TransformInput,
    logger: Logger,
) -> TransformOutput:
    """Transform OpenAPI records to Atlan Atlas entity format."""
    out_dir = Path(input.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_file = out_dir / "openapi_metadata.json"

    connection = input.connection
    if connection is not None:
        conn_qn: str = connection.attributes.qualified_name
    else:
        conn_qn = input.connection_qualified_name
    if not conn_qn:
        raise ValueError(
            "connection or connection_qualified_name is required for transform"
        )
    workflow_id = input.workflow_id
    workflow_type = input.workflow_type
    workflow_run_at_ms = input.workflow_run_at_ms

    api_spec_count = 0
    api_path_count = 0

    with output_file.open("wb") as out_f:
        # Emit Connection only on CREATE — REUSE means the connection already exists
        if connection is not None:
            conn_asset = map_connection(connection)
            out_f.write(conn_asset.to_nested_bytes() + b"\n")

        # Emit APISpec records
        for record in _iter_jsonl(input.api_spec_file, OpenAPISpecRecord):
            asset = map_api_spec(
                record, conn_qn, workflow_id, workflow_type, workflow_run_at_ms
            )
            out_f.write(asset.to_nested_bytes() + b"\n")
            api_spec_count += 1

        # Emit APIPath records
        for record in _iter_jsonl(input.api_path_file, OpenAPIPathRecord):
            asset = map_api_path(
                record, conn_qn, workflow_id, workflow_type, workflow_run_at_ms
            )
            out_f.write(asset.to_nested_bytes() + b"\n")
            api_path_count += 1

    total = api_spec_count + api_path_count
    logger.info(
        "transform complete api_spec_count=%d api_path_count=%d total=%d",
        api_spec_count,
        api_path_count,
        total,
    )

    return TransformOutput(
        output_file=FileReference(
            local_path=str(output_file), tier=StorageTier.RETAINED
        ),
        api_spec_count=api_spec_count,
        api_path_count=api_path_count,
    )


# =============================================================================
# OpenAPI Connector App
# =============================================================================


class OpenAPIConnector(App):
    """OpenAPI Spec Loader connector app.

    Extracts APISpec + APIPath metadata from an OpenAPI spec document and
    transforms it to JSONL format compatible with publish-app.

    Extraction is bundled in a single task (indivisible API: one GET returns
    both APISpec and APIPath data in the same response).

    Tasks:
    1. extract_spec — fetch spec URL, emit api_spec.jsonl + api_path.jsonl
    2. transform — map to Atlan Atlas entity format
    3. publish — upload NDJSON to object storage and call publish-app (if load_to_atlan=True)
    """

    name = "openapi"

    passthrough_modules = {"openapi.asset_mapper"}  # noqa: RUF012

    @task(
        timeout_seconds=1800,
        heartbeat_timeout_seconds=120,
        auto_heartbeat_seconds=30,
    )
    async def download_cloud_spec(
        self, input: DownloadCloudSpecInput
    ) -> DownloadCloudSpecOutput:
        """Download OpenAPI spec from cloud storage. Runs as activity (has I/O)."""
        credential_data = None
        if input.cloud_source:
            from application_sdk.services.secretstore import SecretStore

            credential_data = await SecretStore.get_credentials(input.cloud_source)
            self.logger.info(
                "resolved cloud_source credential keys=%s",
                list(credential_data.keys()),
            )

        from app.cloud_storage import download_spec_from_cloud

        local_spec_paths = await download_spec_from_cloud(
            spec_prefix=input.spec_prefix,
            spec_key=input.spec_key,
            credential_data=credential_data,
            output_dir=input.output_dir,
            tenant_store=self.context.storage,
        )
        return DownloadCloudSpecOutput(spec_paths=local_spec_paths)

    @task(
        timeout_seconds=3600,
        heartbeat_timeout_seconds=120,
        auto_heartbeat_seconds=30,
    )
    async def extract_spec(self, input: ExtractSpecInput) -> ExtractSpecOutput:
        """Fetch the OpenAPI spec URL and extract APISpec + APIPath records."""
        self.logger.info("extract_spec task starting spec_url=%s", input.spec_url)

        if not input.spec_url:
            raise ValueError("spec_url is required for extract_spec")

        # Claim the auth_header that validate() stored in app state, avoiding a
        # second DAPR credential lookup. Falls back to resolve_credential() if
        # state was not populated (e.g. standalone / test execution).
        auth_header = cast("str | None", self.get_app_state(VALIDATED_AUTH_HEADER_KEY))
        if auth_header is not None:
            self.set_app_state(VALIDATED_AUTH_HEADER_KEY, None)  # claim ownership
            self.logger.debug("using pre-validated auth_header from validate()")
        elif input.openapi_credential is not None:
            from app.credentials import OpenAPICredential

            credential = await self.context.resolve_credential(input.openapi_credential)
            auth_header = (
                credential.auth_header
                if isinstance(credential, OpenAPICredential)
                else ""
            )
        else:
            auth_header = ""

        spec_file, path_file, spec_count, path_count = await _extract_spec_async(
            spec_url=input.spec_url,
            connection_qualified_name=input.connection_qualified_name,
            output_dir=input.output_dir,
            auth_header=auth_header,
            logger=self.logger,
        )

        self.logger.info(
            "extract_spec task completed api_spec_count=%d api_path_count=%d",
            spec_count,
            path_count,
        )
        return ExtractSpecOutput(
            api_spec_file=spec_file,
            api_path_file=path_file,
            api_spec_count=spec_count,
            api_path_count=path_count,
        )

    @task(
        timeout_seconds=1800,
        heartbeat_timeout_seconds=120,
        auto_heartbeat_seconds=30,
    )
    async def transform(self, input: TransformInput) -> TransformOutput:
        """Transform OpenAPI records to Atlan Atlas entity format."""
        self.logger.info("transform task starting")

        result = await self.run_in_thread(_transform_blocking, input, self.logger)

        self.logger.info(
            "transform task completed api_spec_count=%d api_path_count=%d",
            result.api_spec_count,
            result.api_path_count,
        )
        return result

    async def run(self, input: OpenAPIConnectorInput) -> OpenAPIConnectorOutput:  # type: ignore[override]
        """Execute the OpenAPI Spec Loader connector workflow.

        Orchestration:
        1. Validate required fields
        2. extract_spec — fetch and parse the OpenAPI spec
        3. transform — map to Atlan Atlas entities (if records were extracted)
        4. publish — sync to Atlan via publish-app (if load_to_atlan=True)
        """
        if input.connection_usage == "REUSE":
            conn_qn = input.connection_qualified_name
            # Fall back to connection.qualified_name when the explicit field is empty
            # (handles callers that provide a connection object without setting connection_usage=CREATE)
            if not conn_qn and input.connection is not None:
                conn_qn = input.connection.attributes.qualified_name
            if not conn_qn:
                raise ValueError(
                    "connection_qualified_name required when connection_usage='REUSE'"
                )
            connection = None
        else:
            connection = self.require(input.connection, "connection")
            conn_qn = connection.attributes.qualified_name

        output_dir = input.output_dir or str(
            Path(tempfile.gettempdir()) / "openapi" / self.run_id
        )

        if input.import_type == "CLOUD":
            if not input.spec_prefix and not input.spec_key:
                raise ValueError(
                    "spec_prefix or spec_key required when import_type='CLOUD'"
                )
            # Download spec from cloud storage via task (credential resolution
            # and cloud I/O must run in an activity, not workflow code).
            cloud_result = await self.download_cloud_spec(
                DownloadCloudSpecInput(
                    cloud_source=input.cloud_source,
                    spec_prefix=input.spec_prefix,
                    spec_key=input.spec_key,
                    output_dir=f"{output_dir}/cloud_download",
                )
            )
            spec_urls = cloud_result.spec_paths
        elif input.import_type == "DIRECT":
            raise ValueError(
                "import_type='DIRECT' is not supported — it was never exposed in the UI. "
                "Use import_type='URL' or import_type='CLOUD'."
            )
        elif input.import_type == "URL":
            if not input.spec_url:
                raise ValueError("spec_url is required when import_type='URL'")
            spec_urls = [input.spec_url]
        else:
            raise ValueError(f"Unknown import_type: {input.import_type}")

        self.logger.info(
            "openapi connector starting connection_qualified_name=%s spec_urls=%s load_to_atlan=%s",
            conn_qn,
            spec_urls,
            input.load_to_atlan,
        )

        # ================================================================
        # Step 1: Extract APISpec + APIPath (bundled, one per spec file)
        # ================================================================
        total_scanned = 0
        all_extract_results: list[ExtractSpecOutput] = []

        for i, spec_url in enumerate(spec_urls):
            extract_result = await self.extract_spec(
                ExtractSpecInput(
                    spec_url=spec_url,
                    connection_qualified_name=conn_qn,
                    output_dir=f"{output_dir}/raw/{i}",
                )
            )
            all_extract_results.append(extract_result)
            total_scanned += (
                extract_result.api_spec_count + extract_result.api_path_count
            )

        # ================================================================
        # Step 2: Transform to Atlan Atlas entity format
        # ================================================================
        api_spec_count = 0
        api_path_count = 0
        output_file_ref: FileReference | None = None

        if total_scanned > 0:
            workflow_run_at_ms = int(self.context.started_at.timestamp() * 1000)
            for i, extract_result in enumerate(all_extract_results):
                if extract_result.api_spec_count + extract_result.api_path_count == 0:
                    continue
                transform_result = await self.transform(
                    TransformInput(
                        api_spec_file=extract_result.api_spec_file,
                        api_path_file=extract_result.api_path_file,
                        connection=connection,
                        connection_qualified_name=conn_qn,
                        output_dir=f"{output_dir}/transform/{i}",
                        workflow_id=self.run_id,
                        workflow_type=self.context.app_name,
                        workflow_run_at_ms=workflow_run_at_ms,
                    )
                )
                output_file_ref = transform_result.output_file
                api_spec_count += transform_result.api_spec_count
                api_path_count += transform_result.api_path_count

        # ================================================================
        # Step 3: Upload and publish to Atlan (if requested)
        # ================================================================
        publish_completed = False
        output_file_path = output_file_ref.local_path if output_file_ref else ""

        transformed_data_prefix = ""

        if input.load_to_atlan and output_file_path:
            # Upload to SDK default path + /transformed/ subdirectory:
            #   artifacts/apps/{app}/workflows/{workflow_id}/{run_id}/transformed/{filename}
            # The SDK computes the prefix from context; we just append /transformed/.
            upload_result = await self.upload(
                UploadInput(
                    local_path=output_file_path,
                    storage_path=f"artifacts/apps/{self.context.app_name}/workflows/{input.workflow_id}/{self.run_id}/transformed/{Path(output_file_path).name}",
                )
            )
            if not upload_result.ref.storage_path:
                raise ValueError(
                    "upload_result.ref.storage_path is None; cannot derive transformed_data_prefix"
                )
            transformed_data_prefix = str(Path(upload_result.ref.storage_path).parent)
            publish_completed = True
            self.logger.info(
                "upload complete transformed_data_prefix=%s", transformed_data_prefix
            )

        self.logger.info(
            "openapi connector completed api_spec_count=%d api_path_count=%d total_scanned=%d publish_completed=%s",
            api_spec_count,
            api_path_count,
            total_scanned,
            publish_completed,
        )

        return OpenAPIConnectorOutput(
            connection_qualified_name=conn_qn
            if input.connection_usage == "REUSE"
            else "",
            transformed_data_prefix=transformed_data_prefix,
            api_spec_count=api_spec_count,
            api_path_count=api_path_count,
            output_file=output_file_ref,
            total_scanned=total_scanned,
            publish_completed=publish_completed,
        )
