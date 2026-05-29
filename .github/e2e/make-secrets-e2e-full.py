"""Write the OpenAPI connector e2e secrets bundle.

The Petstore spec is public and needs no authentication — the bundle
contains an empty auth_header so the connector's OpenAPICredential
is satisfied without any secret injection.
"""

import json
import os
from pathlib import Path

bundle = {
    "openapi-credentials": json.dumps({"auth_header": ""}),
}

# SDR_CONFIG_DIR is set by the SDK's sdr-e2e composite action to whichever
# config dir it resolved (.github/sdr-e2e or .github/e2e). Write there so
# the post-script existence check passes regardless of which dir was picked.
sdr_config_dir = os.environ.get("SDR_CONFIG_DIR", ".github/e2e")
out = Path(sdr_config_dir) / "secrets" / "credentials.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(bundle))
print(f"Secrets bundle written: {out}")
