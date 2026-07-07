# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from __future__ import annotations

from typing import Literal

from pydantic import Field

from application_sdk.testing.e2e.substitutions import MustacheSubstitutions


class OpenapiMustacheSubstitutions(MustacheSubstitutions):
    extraction_method: str = Field(default="direct", alias="{{extraction_method}}")
    import_type: Literal["URL", "CLOUD"] = Field(default="URL", alias="{{import_type}}")
    spec_url: str = Field(default="", alias="{{spec_url}}")
    spec_prefix: str = Field(default="", alias="{{spec_prefix}}")
    spec_key: str = Field(default="", alias="{{spec_key}}")
    cloud_source: str = Field(default="", alias="{{cloud_source}}")
