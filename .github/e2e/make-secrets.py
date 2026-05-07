"""Write the OpenAPI connector CI secrets bundle to .github/e2e/secrets/credentials.json.

The OpenAPI connector uses an optional auth_header for private specs.
The public Petstore spec used in CI needs no auth — empty string.
"""

import json
from pathlib import Path

bundle = {
    "openapi-credentials": json.dumps({"auth_header": ""}),
}

out = Path(".github/e2e/secrets/credentials.json")
out.write_text(json.dumps(bundle))
print(f"Secrets bundle written: {out}")
