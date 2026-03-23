"""
Smoke test for the OpenAPI connector replay fixture.

Verifies that:
  1. The mock intercepts the spec URL and returns the scraped document.
  2. The response body contains expected APISpec fields (info block).
  3. The response body contains expected APIPath records (paths block).
  4. A known path is present with the expected HTTP operations.
  5. The mock works with httpx.AsyncClient (async mode — the App Framework uses async httpx).

Run with:
    uv run pytest extracts/openapi/replay/test_smoke.py -v
"""

import httpx
import pytest

# Import the fixture from the replay conftest (same directory — pytest finds it automatically)


def test_spec_document_returned(mock_openapi_spec, extracts_dir):
    """Mock returns a valid OpenAPI spec document for a GET request to the spec URL."""
    import json

    # Load the spec URL from metadata (same as what the connector will use)
    metadata = json.loads((extracts_dir / "metadata.json").read_text())
    spec_url = metadata["base_url"]

    # Synchronous client — confirms the mock works for sync httpx too
    with httpx.Client() as client:
        response = client.get(spec_url)

    assert response.status_code == 200
    spec = response.json()

    # Basic structure checks
    assert "openapi" in spec, "Response must have 'openapi' field"
    assert "info" in spec, "Response must have 'info' block (APISpec fields)"
    assert "paths" in spec, "Response must have 'paths' block (APIPath records)"


def test_api_spec_fields(mock_openapi_spec, extracts_dir):
    """APISpec metadata fields are present and non-empty in the mocked response."""
    import json

    metadata = json.loads((extracts_dir / "metadata.json").read_text())
    spec_url = metadata["base_url"]

    with httpx.Client() as client:
        response = client.get(spec_url)

    info = response.json()["info"]

    assert info.get("title"), "APISpec name (info.title) must be non-empty"
    assert info.get("version"), "APISpec version (info.version) must be non-empty"
    # Petstore-specific: license and termsOfService should be present
    assert info.get("license"), "License block should be present"
    assert info.get("termsOfService"), "termsOfService should be present"


def test_api_paths_present(mock_openapi_spec, extracts_dir):
    """APIPath records (spec paths) are present and non-empty."""
    import json

    metadata = json.loads((extracts_dir / "metadata.json").read_text())
    spec_url = metadata["base_url"]

    with httpx.Client() as client:
        response = client.get(spec_url)

    paths = response.json()["paths"]

    assert len(paths) >= 1, "Spec must have at least one path (APIPath record)"

    # Verify a known path exists (Petstore specific — adjust for other specs)
    assert "/pet" in paths, "Petstore spec should have /pet path"
    pet_path = paths["/pet"]
    # /pet supports PUT and POST
    assert "put" in pet_path or "post" in pet_path, (
        "/pet should have PUT or POST operations"
    )


def test_templated_path_present(mock_openapi_spec, extracts_dir):
    """At least one templated path (api_path_is_templated=True) is present."""
    import json

    metadata = json.loads((extracts_dir / "metadata.json").read_text())
    spec_url = metadata["base_url"]

    with httpx.Client() as client:
        response = client.get(spec_url)

    paths = response.json()["paths"]
    templated = [p for p in paths if "{" in p and "}" in p]
    assert len(templated) >= 1, (
        "Spec should have at least one templated path (e.g. /pet/{petId})"
    )


@pytest.mark.asyncio
async def test_async_client_works(mock_openapi_spec, extracts_dir):
    """Mock works with httpx.AsyncClient (the App Framework uses async httpx)."""
    import json

    metadata = json.loads((extracts_dir / "metadata.json").read_text())
    spec_url = metadata["base_url"]

    async with httpx.AsyncClient() as client:
        response = await client.get(spec_url)

    assert response.status_code == 200
    spec = response.json()
    assert "paths" in spec
    # At least the number of paths that were scraped
    assert len(spec["paths"]) >= 1
