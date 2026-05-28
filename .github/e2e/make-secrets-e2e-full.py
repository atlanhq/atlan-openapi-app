"""Write the OpenAPI connector e2e secrets bundle.

The Petstore spec is public and needs no authentication — the bundle
contains an empty auth_header so the connector's OpenAPICredential
is satisfied without any secret injection.
"""

import json
from pathlib import Path

bundle = {
    "openapi-credentials": json.dumps({"auth_header": ""}),
}

out = Path(".github/e2e/secrets/credentials.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(bundle))
print(f"Secrets bundle written: {out}")
