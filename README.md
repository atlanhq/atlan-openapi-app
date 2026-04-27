# OpenAPI Spec Loader

Fetches an OpenAPI v3 spec document and registers its metadata as first-class assets in Atlan. One run produces one `APISpec` and one `APIPath` per path defined in the spec.

---

## What it does

1. **Extract** — performs a single `GET` against the spec URL, parsing the response (JSON or YAML) into `APISpec` and `APIPath` records.
2. **Transform** — maps those records to Atlan Atlas entity format (JSONL), stamping qualified names, sync metadata, and parent relationships.
3. **Upload** — writes the JSONL to persistent object storage under `{connection_qn}/transformed-metadata/`.
4. **Publish** — the Automation Engine DAG hands the storage prefix to `publish-app`, which loads assets into Atlan (when `load_to_atlan=true`).

### Assets produced

| Asset | Atlan type | QN pattern | Count |
|---|---|---|---|
| API Spec | `APISpec` | `{connection_qn}/{spec.info.title}` | 1 per run |
| API Path | `APIPath` | `{api_spec_qn}{path_url}` | 1 per path in `spec.paths` |

On the `CREATE` connection path, a `Connection` entity is also emitted ahead of the spec and paths.

---

## Input

Input is defined in [`contract/app.pkl`](contract/app.pkl) and code-generated into [`app/generated/_input.py`](app/generated/_input.py). To regenerate after editing the pkl:

```
make generate
```

### Spec source (`import_type`)

| Value | Behaviour |
|---|---|
| `URL` | HTTP `GET` of `spec_url`. Primary mode. |
| `CLOUD` | Retrieve from object storage using `cloud_source` credential. Not yet implemented. |
| `DIRECT` | UI file upload. Not supported in the App Framework. |

### Connection (`connection_usage`)

| Value | Required fields | Behaviour |
|---|---|---|
| `REUSE` | `connection_qualified_name` | Uses an existing Atlan connection. QN is passed through to the output. |
| `CREATE` | `connection` (ConnectionRef) | Creates a new connection. A `Connection` entity is emitted in the JSONL output. |

### All input fields

| Field | Type | Default | Description |
|---|---|---|---|
| `import_type` | `str` | `"URL"` | Spec source mode — `URL`, `CLOUD`, or `DIRECT`. |
| `spec_url` | `str` | `""` | URL of the OpenAPI JSON/YAML document. Required when `import_type=URL`. |
| `spec_file` | `FileReference` | `None` | Uploaded file reference. Required when `import_type=DIRECT`. |
| `spec_prefix` | `str` | `""` | Object store directory prefix. Required when `import_type=CLOUD`. |
| `spec_key` | `str` | `""` | Object key (filename) in the store. Required when `import_type=CLOUD`. |
| `cloud_source` | `str` | `""` | Object storage credential ID. Required when `import_type=CLOUD`. |
| `connection_usage` | `str` | `"REUSE"` | `CREATE` or `REUSE`. |
| `connection` | `ConnectionRef` | `None` | Connection to create. Required when `connection_usage=CREATE`. |
| `connection_qualified_name` | `str` | `""` | Existing connection QN. Required when `connection_usage=REUSE`. |
| `load_to_atlan` | `bool` | `true` | Upload transformed output and trigger publish-app via the AE DAG. |
| `publish_dry_run` | `bool` | `false` | Upload but skip the Atlas write step in publish-app (`executor_enabled=false`). |
| `output_dir` | `str` | `""` | Local directory for intermediate JSONL files. Defaults to a temp dir. |
| `checkpoint_dir` | `str` | `""` | Enables incremental extraction when set. |

---

## Output

| Field | Type | Description |
|---|---|---|
| `connection_qualified_name` | `str` | The connection QN used for this run. Populated on the `REUSE` path. |
| `transformed_data_prefix` | `str` | Object storage prefix where transformed JSONL was uploaded, e.g. `{conn_qn}/transformed-metadata`. Consumed by the AE DAG publish node. |
| `api_spec_count` | `int` | Number of `APISpec` entities written (0 or 1). |
| `api_path_count` | `int` | Number of `APIPath` entities written. |
| `total_scanned` | `int` | `api_spec_count + api_path_count`. |
| `publish_completed` | `bool` | `true` if the upload step completed (file is in object storage). |
| `output_file` | `FileReference` | Local path to the transformed JSONL file. |

---

## Local development

### Prerequisites

- Python 3.11+, [`uv`](https://github.com/astral-sh/uv)
- [Temporal dev server](https://docs.temporal.io/cli#server)

### Setup

```bash
uv sync
```

### Run

```bash
# 1. Start Temporal
temporal server start-dev --dynamic-config-value frontend.WorkerHeartbeatsEnabled=true

# 2. Configure (public spec — no auth needed)
export OPENAPI_SPEC_URL="https://petstore3.swagger.io/api/v3/openapi.json"

# For private specs with an auth header:
# export OPENAPI_AUTH_HEADER="Bearer <token>"

# To load into Atlan (requires a running publish-app):
# export ATLAN_API_KEY="atl-..."
# export ATLAN_BASE_URL="https://your-tenant.atlan.com"

# 3. Start the dev server
uv run python -m app.run_dev
```

### Trigger a run

```bash
# Extract only (no loading)
curl -X POST http://localhost:8000/workflows/v1/start \
  -H "Content-Type: application/json" \
  -d '{
    "connection_usage": "REUSE",
    "connection_qualified_name": "default/api/test-openapi",
    "spec_url": "https://petstore3.swagger.io/api/v3/openapi.json",
    "load_to_atlan": false
  }'

# Check result (use workflow_id from the response above)
curl http://localhost:8000/workflows/v1/result/<workflow_id>
```

### Tests

```bash
uv run python -m pytest tests/unit -q
```

### Regenerate contract artifacts

```bash
make generate        # regenerates app/generated/_input.py, manifest.json, openapi.json
make check-generate  # fails if generated files are stale (used in CI)
```
