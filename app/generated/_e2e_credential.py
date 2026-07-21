# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from pydantic import Field, BaseModel, ConfigDict

from application_sdk.testing.e2e.credential import CredentialBody


class OpenapiCredentialBodyExtra(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, serialize_by_alias=True)

    aws_role_arn: str = Field(default="", alias="aws_role_arn")
    region: str = Field(default="", alias="region")
    s3_bucket: str = Field(default="", alias="s3_bucket")
    spec_prefix: str = Field(default="", alias="spec_prefix")
    spec_key: str = Field(default="", alias="spec_key")
    gcs_bucket: str = Field(default="", alias="gcs_bucket")
    azure_tenant_id: str = Field(default="", alias="azure_tenant_id")
    storage_account_name: str = Field(default="", alias="storage_account_name")
    adls_container: str = Field(default="", alias="adls_container")


class OpenapiCredentialBody(CredentialBody):
    name: str = Field(alias="name")
    auth_type: str = Field(default="url", alias="authType")
    spec_url: str = Field(default="", alias="spec_url")
    auth_header: str = Field(default="", alias="auth_header")
    username: str = Field(default="", alias="username")
    password: str = Field(default="", alias="password")
    extra: OpenapiCredentialBodyExtra = Field(
        default_factory=OpenapiCredentialBodyExtra, alias="extra"
    )


class OpenapiAgentCredentialBody(CredentialBody):
    name: str = Field(alias="name")
    auth_type: str = Field(default="url", alias="authType")
    connector_config_name: str = Field(
        default="atlan-connectors-openapi", alias="connectorConfigName"
    )
    extra: dict = Field(default_factory=dict, alias="extra")
