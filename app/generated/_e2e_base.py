# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from pyatlan.model.enums import AtlanConnectorType

from application_sdk.testing.e2e import BaseE2ETest  # type: ignore[attr-defined]


class OpenAPIGeneratedE2EBase(BaseE2ETest):
    connector_short_name = "openapi"
    # OpenAPI connections live under default/api/ in the Atlan catalog,
    # not default/openapi/. Overrides BaseE2ETest.connection_type so the
    # harness builds the correct qualifiedName without per-test boilerplate.
    connection_type = AtlanConnectorType.API.value
    # Atlas connection category for API-type connectors.
    connection_category = "API"
    argo_package_name = "@atlan/openapi"
    argo_template_name = "atlan-openapi"
    app_service_url = "http://openapi.openapi-app.svc.cluster.local"
