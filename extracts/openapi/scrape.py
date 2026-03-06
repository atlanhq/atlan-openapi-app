"""
Scrape script for OpenAPI Spec Loader.

No credentials required — scrapes the public Swagger Petstore v3 spec.

Usage:
    uv run python extracts/openapi/scrape.py

    # To scrape a different spec URL:
    export OPENAPI_SPEC_URL="https://example.com/api/openapi.json"
    uv run python extracts/openapi/scrape.py

Output:
    extracts/openapi/raw/spec/response_001.json   — full OpenAPI spec document
    extracts/openapi/raw/spec/headers_001.json    — response status + headers
    extracts/openapi/metadata.json
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default to the Swagger Petstore v3 spec (same URL used in the Kotlin tests).
# Override by setting OPENAPI_SPEC_URL in the environment.
SPEC_URL = os.environ.get(
    "OPENAPI_SPEC_URL",
    "https://petstore3.swagger.io/api/v3/openapi.json",
)

# No sampling needed — the spec is a single document (no pagination).
SAMPLE_LIMIT: int | None = None

# Base output directory (run from repo root: `uv run python extracts/openapi/scrape.py`)
SCRIPT_DIR = Path(__file__).parent
OUTPUT_DIR = SCRIPT_DIR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


SENSITIVE_HEADER_PREFIXES = (
    "set-cookie", "authorization", "x-auth", "x-api-key",
    "cookie", "proxy-authorization",
)
SENSITIVE_HEADER_SUBSTRINGS = ("token", "session", "secret")


def _is_sensitive_header(name: str) -> bool:
    lower = name.lower()
    if any(lower.startswith(p) for p in SENSITIVE_HEADER_PREFIXES):
        return True
    return any(s in lower for s in SENSITIVE_HEADER_SUBSTRINGS)


def save_response(
    response: httpx.Response,
    entity_type: str,
    page: int,
    raw_text: str | None = None,
) -> None:
    """Save response body and filtered headers to raw/<entity_type>/."""
    out_dir = ensure_dir(OUTPUT_DIR / "raw" / entity_type)

    # Save the body — prefer JSON parse; fall back to raw text for YAML responses
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        body = response.json()
    else:
        # YAML or unknown — save as a JSON wrapper so the replay layer can serve it
        body = {"_raw_text": raw_text or response.text, "_content_type": content_type}

    with open(out_dir / f"response_{page:03d}.json", "w") as f:
        json.dump(body, f, indent=2)

    filtered_headers = {
        k: v for k, v in response.headers.items()
        if not _is_sensitive_header(k)
    }
    with open(out_dir / f"headers_{page:03d}.json", "w") as f:
        json.dump(
            {
                "status_code": response.status_code,
                "headers": filtered_headers,
            },
            f,
            indent=2,
        )


def request_with_backoff(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_rate_limit_attempts: int = 5,
    max_waf_attempts: int = 10,
    **kwargs,
) -> httpx.Response:
    """Make an HTTP request with exponential backoff on rate limiting and WAF challenges."""
    rate_attempts = 0
    waf_attempts = 0
    while True:
        response = getattr(client, method)(url, **kwargs)
        if response.status_code == 429:
            rate_attempts += 1
            if rate_attempts >= max_rate_limit_attempts:
                raise RuntimeError(f"Still rate-limited after {max_rate_limit_attempts} attempts: {url}")
            retry_after = int(response.headers.get("Retry-After", 2 ** rate_attempts))
            print(f"  Rate limited. Waiting {retry_after}s (attempt {rate_attempts}/{max_rate_limit_attempts})...")
            time.sleep(retry_after)
            continue
        # WAF detection: HTML body on 200 or 403 = challenge page, not real response.
        # Check BEFORE auth guard so WAF 403s are retried, not treated as auth failures.
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type and response.status_code in (200, 403):
            waf_attempts += 1
            if waf_attempts >= max_waf_attempts:
                raise RuntimeError(f"WAF challenge not resolved after {max_waf_attempts} attempts: {url}")
            wait = min(2 ** waf_attempts, 120)
            print(f"  WAF challenge (HTML body, status {response.status_code}). Waiting {wait}s...")
            time.sleep(wait)
            continue
        if response.status_code in (401, 403):
            print(f"\nERROR: Authentication failed on {url}")
            print(f"  Status: {response.status_code}")
            print(f"  Response: {response.text[:500]}")
            sys.exit(1)
        response.raise_for_status()
        return response


# ---------------------------------------------------------------------------
# Scrape function
# ---------------------------------------------------------------------------


def scrape_spec(client: httpx.Client) -> dict:
    """Fetch the OpenAPI spec document.

    This connector's "API" is a single HTTP GET of a JSON/YAML document.
    No authentication, no pagination — one request, one file.

    Both APISpec and APIPath data are embedded in this single response:
      - APISpec: from spec['info'] + root fields
      - APIPath: from spec['paths']
    """
    entity_type = "spec"
    ensure_dir(OUTPUT_DIR / "raw" / entity_type)

    print(f"Fetching OpenAPI spec from: {SPEC_URL}")
    response = request_with_backoff(client, "get", SPEC_URL)

    # Count paths for stats reporting
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        data = response.json()
    else:
        import yaml  # type: ignore
        data = yaml.safe_load(response.text)

    path_count = len(data.get("paths", {}))
    spec_title = data.get("info", {}).get("title", "<unknown>")
    openapi_version = data.get("openapi", data.get("swagger", "?"))

    save_response(response, entity_type, 1)

    print(f"  Spec: \"{spec_title}\" (OpenAPI {openapi_version})")
    print(f"  Paths: {path_count}")
    print(f"  Done: 1 document saved (1 APISpec + {path_count} APIPath records)")

    # Return counts for metadata.json
    # records = 1 spec + N paths (everything that will become Atlan entities)
    return {"files": 1, "records": 1 + path_count}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    scraped_at = datetime.now(timezone.utc).isoformat()
    entity_stats: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []

    with httpx.Client(timeout=60.0) as client:
        try:
            entity_stats["spec"] = scrape_spec(client)
        except SystemExit:
            raise
        except Exception:
            failures.append(("spec", traceback.format_exc()))
            print("  ERROR fetching spec\n")

    # Write metadata.json
    metadata = {
        "connector_name": "openapi",
        "connector_style": "http",
        "scraped_at": scraped_at,
        "base_url": SPEC_URL,
        "scope": {"spec_url": SPEC_URL},
        "entity_types": entity_stats,
        "sampling": None,
    }
    with open(OUTPUT_DIR / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    print("\nWrote metadata.json")

    print("\n=== Scrape complete ===")
    for name, stats in entity_stats.items():
        print(f"  {name:30s} {stats['records']:6d} records in {stats['files']} file(s)")

    if failures:
        print("\n=== SCRAPE FAILURES ===")
        for name, tb in failures:
            print(f"\n--- {name} ---")
            print(tb)
        sys.exit(1)


if __name__ == "__main__":
    main()
