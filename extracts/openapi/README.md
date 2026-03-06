# OpenAPI Connector — Extract Files

Scraped live data from the **Swagger Petstore v3** public OpenAPI spec for use in
credential-free replay tests.

## What Was Scraped

| Entity Type | Source | Records |
|-------------|--------|---------|
| `spec` (raw document) | `https://petstore3.swagger.io/api/v3/openapi.json` | 14 (1 APISpec + 13 APIPath) |

Scraped: see `metadata.json` for exact timestamp.

**Scope**: Single spec document — Swagger Petstore OpenAPI 3.0.4, 13 paths.

## File Structure

```
extracts/openapi/
  scrape.py              — scrape script (re-run to refresh)
  scrub.py               — PII redactor (run after every scrape)
  metadata.json          — scrape summary + entity counts
  raw/
    spec/
      response_001.json  — full OpenAPI spec document (JSON)
      headers_001.json   — HTTP response status + safe headers
  replay/
    conftest.py          — respx-based replay fixture
    test_smoke.py        — smoke tests (verify replay works)
```

## How to Re-Scrape

No credentials are needed for the default Petstore spec.

```bash
# Default: Petstore v3
uv run python extracts/openapi/scrape.py

# Custom spec URL:
export OPENAPI_SPEC_URL="https://your-api.example.com/openapi.json"
uv run python extracts/openapi/scrape.py
```

> **PII Warning**: Always run `scrub.py` immediately after re-scraping to redact
> email addresses or other PII that may appear in the spec document before committing
> or sharing the extract files.

```bash
uv run python extracts/openapi/scrub.py
```

## How to Use Replay Fixtures in Tests

The `mock_openapi_spec` fixture in `replay/conftest.py` intercepts all `httpx` calls to
the spec URL and returns the scraped document — no network access needed.

### Import path

```python
# Option 1: pytest discovers it automatically if conftest.py is in the same dir tree
# Option 2: explicit import
from extracts.openapi.replay.conftest import mock_openapi_spec, extracts_dir
```

### Fixture name

`mock_openapi_spec` — yields a `respx.MockRouter`

### Example test

```python
import httpx
import pytest


def test_my_connector(mock_openapi_spec, extracts_dir):
    import json

    # Read spec URL from metadata (what the connector will use at runtime)
    metadata = json.loads((extracts_dir / "metadata.json").read_text())
    spec_url = metadata["base_url"]

    # All httpx calls to spec_url are intercepted — no real network call
    with httpx.Client() as client:
        response = client.get(spec_url)

    spec = response.json()
    assert spec["info"]["title"] == "Swagger Petstore - OpenAPI 3.0"
    assert len(spec["paths"]) == 13


@pytest.mark.asyncio
async def test_async_connector(mock_openapi_spec, extracts_dir):
    import json

    metadata = json.loads((extracts_dir / "metadata.json").read_text())
    spec_url = metadata["base_url"]

    async with httpx.AsyncClient() as client:
        response = await client.get(spec_url)

    spec = response.json()
    assert "paths" in spec
```

### Run smoke tests

```bash
uv run pytest extracts/openapi/replay/test_smoke.py -v
```
