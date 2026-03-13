"""OpenAPI Spec Loader Connector App.

Extracts metadata from an OpenAPI v3 spec document (APISpec + APIPath) and
transforms it to JSONL format compatible with the Atlan Loader.

The connector:
1. Validates Atlan credentials (if loading)
2. Fetches the OpenAPI spec document via a single HTTP GET
3. Parses the spec into APISpec (1) and APIPath (N) records — bundled in one task
4. Runs per-type change detection (if checkpoint_dir provided)
5. Transforms to Atlan asset format using pyatlan built-in types
6. Loads to Atlan via atlan-loader child app (if requested)

Extraction pattern: per-scope-item bundled (indivisible API — one GET returns
both APISpec and APIPath data; splitting would require downloading twice).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, TypeVar, cast

import msgspec
from temporalio import workflow

from app_framework.app import App, FileReference, Logger, task
from app_framework.app.contracts import (
    CommitCheckpointInput,
    PrepareCheckpointInput,
    SyncLocalToStorageInput,
    UploadStagedCheckpointInput,
)
from app_framework.change_detection import ChangeDetector, ChangeType, RecordKey
from atlan_loader.contracts import AtlanLoaderInput, AtlanLoaderOutput
from openapi.api_types import OpenAPIPathRecord, OpenAPISpecRecord
from openapi.asset_mapper import (
    build_api_path_qn,
    build_api_spec_qn,
    map_api_path,
    map_api_spec,
    map_connection,
)
from openapi.contracts import (
    DiffInput,
    DiffOutput,
    ExtractSpecInput,
    ExtractSpecOutput,
    OpenAPIConnectorInput,
    OpenAPIConnectorOutput,
    TransformInput,
    TransformOutput,
)
from openapi.credentials import VALIDATED_AUTH_HEADER_KEY

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
    from openapi.api_client import OpenAPIApiClient

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
        "spec extracted",
        extra={"api_spec_count": spec_count, "api_path_count": path_count},
    )
    return (
        FileReference.from_local(spec_file_path),
        FileReference.from_local(path_file_path),
        spec_count,
        path_count,
    )


# =============================================================================
# Diff helper
# =============================================================================


def _diff_blocking(
    input: DiffInput,
    logger: Logger,
) -> DiffOutput:
    """Run change detection across APISpec and APIPath records."""
    out_dir = Path(input.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn_qn: str = input.connection_qualified_name

    total_new = 0
    total_changed = 0
    total_unchanged = 0
    total_deleted = 0
    total_scanned = 0

    logger.info("starting change detection", checkpoint_dir=input.checkpoint_dir)

    def _get_key(record: Any) -> RecordKey:
        if isinstance(record, OpenAPISpecRecord):
            return RecordKey("APISpec", build_api_spec_qn(conn_qn, record.title))
        if isinstance(record, OpenAPIPathRecord):
            return RecordKey(
                "APIPath",
                build_api_path_qn(record.spec_qualified_name, record.path_url),
            )
        raise NotImplementedError(f"unknown record type: {type(record)}")

    def _get_content(record: Any) -> dict[str, Any]:
        if isinstance(record, OpenAPISpecRecord):
            return {
                "name": record.title,
                "api_spec_type": record.openapi_version,
                "api_spec_version": record.spec_version,
                "description": record.description,
            }
        if isinstance(record, OpenAPIPathRecord):
            return {
                "name": record.path_url,
                "api_path_raw_uri": record.path_url,
                "api_path_summary": record.summary,
                "api_path_available_operations": sorted(record.available_operations),
                "api_path_is_templated": record.is_templated,
            }
        raise NotImplementedError(f"unknown record type: {type(record)}")

    def _all_records() -> Any:
        yield from _iter_jsonl(input.api_spec_file, OpenAPISpecRecord)
        yield from _iter_jsonl(input.api_path_file, OpenAPIPathRecord)

    detector: ChangeDetector[Any] = ChangeDetector(
        checkpoint_dir=input.checkpoint_dir,
        get_key=_get_key,
        get_content=_get_content,
    )

    api_spec_path = out_dir / "changed_api_spec.jsonl"
    api_path_path = out_dir / "changed_api_path.jsonl"

    try:
        with api_spec_path.open("wb") as spec_f, api_path_path.open("wb") as path_f:
            for change in detector.stream_changes(_all_records()):
                total_scanned += 1
                if change.change_type == ChangeType.NEW:
                    total_new += 1
                elif change.change_type == ChangeType.CHANGED:
                    total_changed += 1
                else:
                    total_unchanged += 1
                    continue
                record = change.record
                if isinstance(record, OpenAPISpecRecord):
                    spec_f.write(_encoder.encode(record) + b"\n")
                elif isinstance(record, OpenAPIPathRecord):
                    path_f.write(_encoder.encode(record) + b"\n")

        for _ in detector.detect_deletions():
            total_deleted += 1

        detector.stage()
    except Exception:
        detector.rollback()
        raise

    logger.info(
        "change detection complete",
        extra={
            "new": total_new,
            "changed": total_changed,
            "unchanged": total_unchanged,
            "deleted": total_deleted,
            "total": total_scanned,
        },
    )

    return DiffOutput(
        changed_api_spec_file=FileReference.from_local(api_spec_path),
        changed_api_path_file=FileReference.from_local(api_path_path),
        new_count=total_new,
        changed_count=total_changed,
        unchanged_count=total_unchanged,
        deleted_count=total_deleted,
        total_scanned=total_scanned,
        checkpoint_new_path=str(detector.staged_dir),
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
    output_file = out_dir / "openapi_metadata.jsonl"

    connection = input.connection
    if connection is not None:
        _raw_conn_qn = connection.qualified_name
        conn_qn: str = _raw_conn_qn if isinstance(_raw_conn_qn, str) else ""
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
        for record in _iter_jsonl(input.changed_api_spec_file, OpenAPISpecRecord):
            asset = map_api_spec(
                record, conn_qn, workflow_id, workflow_type, workflow_run_at_ms
            )
            out_f.write(asset.to_nested_bytes() + b"\n")
            api_spec_count += 1

        # Emit APIPath records
        for record in _iter_jsonl(input.changed_api_path_file, OpenAPIPathRecord):
            asset = map_api_path(
                record, conn_qn, workflow_id, workflow_type, workflow_run_at_ms
            )
            out_f.write(asset.to_nested_bytes() + b"\n")
            api_path_count += 1

    total = api_spec_count + api_path_count
    logger.info(
        "transform complete",
        extra={
            "api_spec_count": api_spec_count,
            "api_path_count": api_path_count,
            "total": total,
        },
    )

    return TransformOutput(
        output_file=FileReference.from_local(output_file),
        api_spec_count=api_spec_count,
        api_path_count=api_path_count,
    )


# =============================================================================
# OpenAPI Connector App
# =============================================================================


class OpenAPIConnector(App):
    """OpenAPI Spec Loader connector app.

    Extracts APISpec + APIPath metadata from an OpenAPI spec document and
    transforms it to JSONL format compatible with the Atlan Loader.

    Extraction is bundled in a single task (indivisible API: one GET returns
    both APISpec and APIPath data in the same response).

    Tasks:
    1. extract_spec — fetch spec URL, emit api_spec.jsonl + api_path.jsonl
    2. diff — change detection (if checkpoint_dir provided)
    3. transform — map to Atlan Atlas entity format
    4. load — sync to Atlan via atlan-loader child app (if load_to_atlan=True)

    Supports incremental extraction via change detection when checkpoint_dir
    is provided.
    """

    name = "openapi"

    passthrough_modules = {"openapi.asset_mapper"}  # noqa: RUF012

    @task(
        timeout_seconds=3600,
        heartbeat_timeout_seconds=120,
        auto_heartbeat_seconds=30,
    )
    async def extract_spec(self, input: ExtractSpecInput) -> ExtractSpecOutput:
        """Fetch the OpenAPI spec URL and extract APISpec + APIPath records."""
        self.logger.info("extract_spec task starting", spec_url=input.spec_url)

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
            from openapi.credentials import OpenAPICredential

            credential = await self.resolve_credential(input.openapi_credential)
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
            "extract_spec task completed",
            api_spec_count=spec_count,
            api_path_count=path_count,
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
    async def diff(self, input: DiffInput) -> DiffOutput:
        """Run change detection for APISpec and APIPath records."""
        self.logger.info("diff task starting", checkpoint_dir=input.checkpoint_dir)

        result = await self.run_in_thread(_diff_blocking, input, self.logger)

        self.logger.info(
            "diff task completed",
            new=result.new_count,
            changed=result.changed_count,
            unchanged=result.unchanged_count,
            deleted=result.deleted_count,
        )
        return result

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
            "transform task completed",
            api_spec_count=result.api_spec_count,
            api_path_count=result.api_path_count,
        )
        return result

    async def run(self, input: OpenAPIConnectorInput) -> OpenAPIConnectorOutput:  # type: ignore[override]
        """Execute the OpenAPI Spec Loader connector workflow.

        Orchestration:
        1. Validate required fields
        2. extract_spec — fetch and parse the OpenAPI spec
        3. diff — change detection (if checkpoint_dir provided)
        4. transform — map to Atlan Atlas entities (if there are changes)
        5. load — sync to Atlan (if load_to_atlan=True and there are changes)
        6. commit checkpoint (after all stages succeed)
        """
        if input.connection_usage == "REUSE":
            if not input.connection_qualified_name:
                raise ValueError(
                    "connection_qualified_name required when connection_usage='REUSE'"
                )
            conn_qn = input.connection_qualified_name
            connection = None
        else:
            connection = self.require(input.connection, "connection")
            conn_qn = (
                connection.qualified_name
                if isinstance(connection.qualified_name, str)
                else ""
            )

        if input.import_type == "CLOUD":
            if not input.cloud_source or not input.spec_key:
                raise ValueError(
                    "cloud_source and spec_key required when import_type='CLOUD'"
                )
            raise NotImplementedError(
                "CLOUD import_type not yet implemented in App Framework. "
                "Use import_type='URL' with a direct spec URL instead."
            )

        if input.import_type == "DIRECT":
            raise ValueError(
                "import_type='DIRECT' (UI file upload) is not supported in the App Framework. "
                "Use import_type='URL' with a publicly accessible spec URL, or "
                "import_type='CLOUD' with a presigned URL."
            )

        if not input.spec_url:
            raise ValueError("spec_url is required")

        self.logger.info(
            "openapi connector starting",
            connection_qualified_name=conn_qn,
            spec_url=input.spec_url,
            load_to_atlan=input.load_to_atlan,
            checkpoint_enabled=bool(input.checkpoint_dir),
        )

        output_dir = input.output_dir or str(
            Path(tempfile.gettempdir()) / "openapi" / self.run_id
        )

        # ================================================================
        # Step 0: Pre-check Atlan credentials (skip in dry-run mode)
        # ================================================================
        if input.load_to_atlan and not input.loader_dry_run:
            from app_framework.handler import PreCheckInput

            atlan_credential = self.require(
                input.atlan_credential, "atlan_credential", "when load_to_atlan=True"
            )
            self.logger.debug("validating Atlan credentials")
            pre_check_result = await self.pre_check(
                PreCheckInput(credential_refs=[atlan_credential])
            )
            self.logger.info(
                "Atlan credentials validated",
                identities=pre_check_result.auth_output.identities
                if pre_check_result.auth_output
                else None,
            )

        # ================================================================
        # Step 1: Extract APISpec + APIPath (bundled, one HTTP GET)
        # ================================================================
        extract_result = await self.extract_spec(
            ExtractSpecInput(
                spec_url=input.spec_url,
                connection_qualified_name=conn_qn,
                output_dir=f"{output_dir}/raw",
                openapi_credential=input.openapi_credential,
            )
        )

        # Total scanned = APISpec + APIPath records (Connection is always emitted
        # unconditionally and is not subject to change detection)
        total_scanned = extract_result.api_spec_count + extract_result.api_path_count

        # ================================================================
        # Step 2: Change detection (if checkpoint_dir provided)
        # ================================================================
        new_count = 0
        changed_count = 0
        unchanged_count = 0
        deleted_count = 0
        checkpoint_ref: FileReference | None = None
        diff_result = None

        # File references passed to transform
        changed_api_spec_file = extract_result.api_spec_file
        changed_api_path_file = extract_result.api_path_file

        if input.checkpoint_dir:
            checkpoint_key = self.checkpoint_storage_key(input)

            await self.prepare_checkpoint(
                PrepareCheckpointInput(
                    checkpoint_dir=input.checkpoint_dir,
                    storage_key=checkpoint_key,
                )
            )

            diff_result = await self.diff(
                DiffInput(
                    api_spec_file=extract_result.api_spec_file,
                    api_path_file=extract_result.api_path_file,
                    connection_qualified_name=conn_qn,
                    checkpoint_dir=input.checkpoint_dir,
                    output_dir=f"{output_dir}/diff",
                )
            )

            await self.upload_staged_checkpoint(
                UploadStagedCheckpointInput(
                    checkpoint_new_path=diff_result.checkpoint_new_path,
                    storage_key=checkpoint_key,
                )
            )

            changed_api_spec_file = diff_result.changed_api_spec_file
            changed_api_path_file = diff_result.changed_api_path_file
            new_count = diff_result.new_count
            changed_count = diff_result.changed_count
            unchanged_count = diff_result.unchanged_count
            deleted_count = diff_result.deleted_count
        else:
            # No change detection — all records are considered "new"
            new_count = total_scanned

        # ================================================================
        # Step 3: Transform to Atlan Atlas entity format
        # ================================================================
        has_changes = new_count + changed_count > 0

        api_spec_count = 0
        api_path_count = 0
        output_file_ref: FileReference | None = None

        if has_changes:
            info = workflow.info()
            workflow_run_at_ms = int(info.start_time.timestamp() * 1000)
            transform_result = await self.transform(
                TransformInput(
                    changed_api_spec_file=changed_api_spec_file,
                    changed_api_path_file=changed_api_path_file,
                    connection=connection,
                    connection_qualified_name=conn_qn,
                    output_dir=output_dir,
                    workflow_id=info.workflow_id,
                    workflow_type=info.workflow_type,
                    workflow_run_at_ms=workflow_run_at_ms,
                )
            )
            output_file_ref = transform_result.output_file
            api_spec_count = transform_result.api_spec_count
            api_path_count = transform_result.api_path_count

        # ================================================================
        # Step 4: Load to Atlan (if requested and there are changes)
        # ================================================================
        durable_file_ref: FileReference | None = None
        atlan_loaded = 0
        atlan_created = 0
        atlan_updated = 0
        atlan_validated = 0
        atlan_error_count = 0
        atlan_errors: list = []

        output_file_path = output_file_ref.local_path if output_file_ref else ""

        if (
            input.load_to_atlan
            and has_changes
            and output_file_path
            and (input.atlan_credential or input.loader_dry_run)
        ):
            total_assets = api_spec_count + api_path_count + (1 if connection else 0)

            sync_result = await self.sync_local_to_storage(
                SyncLocalToStorageInput(
                    local_path=output_file_path,
                    key="openapi_metadata.jsonl",
                    content_type="application/x-ndjson",
                )
            )
            durable_file_ref = sync_result.ref

            self.logger.info("starting atlan loader", total_assets=total_assets)

            loader_result = await self.call_by_name(
                "atlan-loader",
                AtlanLoaderInput(
                    jsonl_files=[durable_file_ref],
                    atlan_credential=input.atlan_credential,
                    output_dir=f"{output_dir}/loader_temp",
                    batch_size=input.loader_batch_size,
                    chunk_size=input.loader_chunk_size,
                    max_chunks_per_execution=input.loader_max_chunks_per_execution,
                    save_timeout=input.loader_save_timeout,
                    dry_run=input.loader_dry_run,
                    dry_run_for_creation=input.loader_dry_run,
                    jsonl_format="nested",  # to_nested_bytes() produces Atlas JSON format
                ),
                output_type=AtlanLoaderOutput,
                task_queue="atlan-loader-queue",
            )

            atlan_loaded = loader_result.total_loaded
            atlan_created = loader_result.created_count
            atlan_updated = loader_result.updated_count
            atlan_validated = loader_result.validated_count
            atlan_error_count = loader_result.error_count
            atlan_errors = loader_result.errors

            if atlan_errors:
                first_err = atlan_errors[0]
                first_error_msg = getattr(first_err, "message", str(first_err))
                self.logger.warning(
                    "atlan loader completed with errors",
                    loaded_count=atlan_loaded,
                    error_count=atlan_error_count,
                    first_error=first_error_msg,
                )
            else:
                self.logger.info(
                    "atlan loader complete",
                    loaded_count=atlan_loaded,
                    created_count=atlan_created,
                    updated_count=atlan_updated,
                )

        # ================================================================
        # Commit checkpoint (only after all stages succeed)
        # ================================================================
        if input.checkpoint_dir and diff_result is not None:
            commit_result = await self.commit_checkpoint(
                CommitCheckpointInput(
                    checkpoint_dir=input.checkpoint_dir,
                    checkpoint_new_path=diff_result.checkpoint_new_path,
                    storage_key=checkpoint_key,
                )
            )
            checkpoint_ref = commit_result.checkpoint_ref

        self.logger.info(
            "openapi connector completed",
            api_spec_count=api_spec_count,
            api_path_count=api_path_count,
            total_scanned=total_scanned,
            new=new_count,
            changed=changed_count,
            unchanged=unchanged_count,
            deleted=deleted_count,
            atlan_loaded=atlan_loaded,
        )

        return OpenAPIConnectorOutput(
            api_spec_count=api_spec_count,
            api_path_count=api_path_count,
            output_file=durable_file_ref or output_file_ref,
            total_scanned=total_scanned,
            new_count=new_count,
            changed_count=changed_count,
            unchanged_count=unchanged_count,
            deleted_count=deleted_count,
            checkpoint_ref=checkpoint_ref,
            atlan_loaded_count=atlan_loaded,
            atlan_created_count=atlan_created,
            atlan_updated_count=atlan_updated,
            atlan_validated_count=atlan_validated,
            atlan_error_count=atlan_error_count,
            atlan_errors=atlan_errors,
        )
