# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from application_sdk.testing.e2e import BaseE2ETest  # type: ignore[attr-defined]


class OpenAPIGeneratedE2EBase(BaseE2ETest):
    connector_short_name = "openapi"
    argo_package_name = "@atlan/openapi"
    argo_template_name = "atlan-openapi"
    app_service_url = "http://openapi.openapi-app.svc.cluster.local"
