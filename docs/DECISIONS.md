# Crossover 2.0 Migration — Decision Log

**Date**: 2026-03-26
**Branch**: `contract-parity`
**Context**: Migrating `csa-openapi-spec-loader` from Argo/Kotlin to App SDK v3 with Crossover 2.0 deployment.

---

## Decision 1: Task Queue Name → `openapi-queue`

**Problem**: The task queue name was inconsistent across 5 locations — SDK auto-derives `open-api-connector-queue` from `OpenAPIConnector`, tests use `openapi-queue`, SDR uses `openapi-connector`, helm uses `openapi`, and `manifest.json` uses `atlan-openapi-{deployment_name}`.

**Decision**: Use `openapi-queue` everywhere, set via `ENV ATLAN_TASK_QUEUE=openapi-queue` in Dockerfile.

**Rationale**:
- Tests already use `openapi-queue` — no test changes needed.
- Explicit env var overrides SDK auto-derivation — no surprises.
- SDK resolution order: CLI arg → `ATLAN_TASK_QUEUE` env → auto-derived. Env var is the cleanest override.

**Changes**: Dockerfile (`ENV ATLAN_TASK_QUEUE`), crossover template (`task-queue` param).

---

## Decision 2: Publish Path → Crossover Template Handles It

**Problem**: Three possible publish approaches — app publishes internally, crossover Argo template publishes, or AE DAG publishes (current).

**Decision**: Crossover Argo template handles publish. The app only extracts, transforms, and uploads JSONL. Argo triggers `convert-transformed-output-format` → `atlan-crossover-publish` after the app completes.

**Rationale**:
- This is the standard Crossover 2.0 pattern (Hightouch, Oracle, etc. all do this).
- Separation of concerns — the app doesn't need to know about publish-app internals.
- Publish-app handles connection creation, diff, and Atlas writes.
- The AE DAG (`manifest.json`) was Chris's interim approach — Crossover 2.0 replaces it.

**Changes**: Marketplace template gets `convert-transformed-output-format` + publish steps. App's `run()` continues to upload JSONL to object store as before.

---

## Decision 3: CLOUD Import Mode → Implement for Full Parity

**Problem**: CLOUD mode (`import_type=CLOUD`) raises `NotImplementedError` in v3. Old Kotlin connector fully supports it. Atlan docs list "Object storage" as a supported import mode.

**Decision**: Implement CLOUD mode. Use Dapr objectstore binding from app-sdk to download the spec file.

**Rationale**:
- Atlan docs (docs.atlan.com/platform/references/openapi-spec-loader) explicitly list "Object storage" as a supported import mode.
- Customers using S3/GCS/ADLS to host their specs would break without this.
- The UI configmap (`configmaps/default.yaml`) exposes CLOUD as a radio option alongside URL.

**Implementation** (from Kotlin source `Utils.getInputFile()`):
- **Path A** (cloud_source provided with valid auth): Resolve credential via Dapr secretstore → parse `authType` (s3/gcs/adls) → create `obstore` store (S3Store/GCSStore/AzureStore) with the credential's own bucket/keys → download `spec_prefix/spec_key`. All storage info comes from the resolved credential. S3 role ARN is handled natively by obstore (no boto3 needed).
- **Path B** (no cloud_source or credential has no auth): Use the tenant's Dapr-configured object store (`self.context.storage`) — already set up by Helm/K8s. No env vars needed. The same store that `self.upload()` / `self.download()` uses.

Uses `obstore` (already bundled in app-sdk) for all cloud storage I/O — zero new dependencies. The old Argo/Kotlin approach used env vars (`CLOUD_PROVIDER`, `AWS_S3_BUCKET_NAME`, etc.) for tenant storage — in v3, Dapr replaces all of that.

Also supports prefix-only mode (no `spec_key`): lists all objects under `spec_prefix`, filters to `.json/.yaml/.yml/.zip`, downloads and processes each.

**Status**: Implemented in `app/cloud_storage.py`.

---

## Decision 4: DIRECT Import Mode → Do Not Implement (Dead Code)

**Problem**: Old Argo template had `spec_file`, `spec_file_key`, `spec_file_id` parameters and a `move` step for DIRECT mode.

**Decision**: Do not implement. Remove from considerations.

**Rationale**:
- The UI configmap radio widget only shows `URL` and `CLOUD` — DIRECT is not in the `enum`.
- The `spec_file` widget has `"hidden": true` — never visible to users.
- The `anyOf` schema has a DIRECT entry but it's unreachable from the UI.
- This is confirmed dead code that was wired in the old template but never exposed.

**Changes**: No `spec_file` params in crossover template. No DIRECT handling in app code.

---

## Decision 5: `user_id` / Impersonation → Not Needed, Remove

**Problem**: Old Argo template injects `ATLAN_USER_ID` from workflow creator. Crossover typically passes `user_id` in `workflow-arguments.metadata`.

**Decision**: Do not pass `user_id` in workflow-arguments. The app does not need it.

**Rationale**:
- The openapi app does NOT call any Atlan/pyatlan APIs. Zero `AtlanClient` usage.
- It only: HTTP GETs a spec URL → parses JSON/YAML → produces JSONL → uploads to object store.
- Publish-app handles all Atlas writes with its own auth (OAuth2 via Dapr).
- `user_id` is only needed for apps that call `get_client_async(impersonate_user_id=...)` (like user-offboarding).
- The v3 app's `AppInputContract` has no `user_id` field and the SDK `Input` base class doesn't auto-extract it.

**Changes**: `user_id` omitted from crossover template `workflow-arguments`.

---

## Decision 6: Connection Handling → Publish-App Decides

**Problem**: How does connection CREATE vs REUSE flow through crossover to publish-app?

**Decision**: The app passes `connection_qualified_name` and optionally emits a `Connection` entity in the JSONL. The crossover template passes `connection` and `connection_creation_enabled` to the publish step. Publish-app handles the actual creation/reuse logic.

**Rationale** (from reading publish-app source):
- **REUSE**: publish-app receives `connection_qualified_name`, `connection_creation_enabled=false`. It searches by QN, finds it, skips creation.
- **CREATE**: publish-app receives `connection_qualified_name`, `connection_creation_enabled=true`, `connection_entity` (full dict). It searches by QN, doesn't find it, creates using the entity, waits for policy sync.
- The app's `map_connection()` already produces the Connection entity in JSONL output.
- The crossover template passes the raw `connection` JSON from the wizard to both the app (for QN derivation) and publish (as `connection_entity`).

**Changes**: Crossover template wires `connection` through to publish step params.

---

## Decision 7: `spec_file` in Crossover Template → Not Added

**Problem**: Old template had `spec_file` parameters for DIRECT mode.

**Decision**: Do not add to crossover template.

**Rationale**: Follows from Decision 4 (DIRECT is dead code). `spec_prefix` and `spec_key` for CLOUD mode ARE included since CLOUD is a real user-facing feature.

---

## Parity Status

| Feature | Old (Argo/Kotlin) | V3 App | Crossover Template | Status |
|---------|-------------------|--------|-------------------|--------|
| URL import | Yes | Yes | Yes | Parity |
| CLOUD import | Yes | Implemented (Path A + B) | Params wired | Parity |
| DIRECT import | Wired but hidden in UI | ValueError | Not included | Dead code — skipped |
| Connection REUSE | Yes | Yes | Wired | Parity |
| Connection CREATE | Yes | map_connection() exists | Wired to publish | Needs E2E validation |
| Publish | Java AssetBatch | Upload to object store | Crossover publish step | Parity via crossover |
| ZIP support | Yes (Kotlin SwaggerParser) | Yes (api_client.py) | N/A | Parity |
| YAML support | Yes | Yes | N/A | Parity |
| APISpec extraction | Yes | Yes | N/A | Parity |
| APIPath extraction | Yes | Yes | N/A | Parity |
| Markdown description table | Yes | Yes (exact format match) | N/A | Parity |
| Auth header for private specs | Yes (credential) | Yes (OpenAPICredential) | N/A | Parity |

---

## Reference Docs

- Atlan docs: https://docs.atlan.com/platform/references/openapi-spec-loader
- Old Argo template: `marketplace-packages/packages/csa/openapi-spec-loader/templates/default.yaml`
- Old configmap: `marketplace-packages/packages/csa/openapi-spec-loader/configmaps/default.yaml`
- Publish-app source: `atlan-publish-app/app/lib/connection/processor.py`
- Crossover 2.0 reference: `marketplace-packages/packages/csa/api-token-connection-admin/templates/default.yaml` (temp-test-vc-0324 branch)
- Hightouch crossover reference: `marketplace-packages/packages/csa/hightouch/templates/default.yaml`
