# Generated from contract/app.pkl via contract-toolkit. DO NOT EDIT.
# Regenerate with: pkl eval -m . contract/app.pkl
from __future__ import annotations

from typing import Literal

from pydantic import Field

from application_sdk.testing.e2e.substitutions import MustacheSubstitutions


class OpenapiMustacheSubstitutions(MustacheSubstitutions):
    extraction_method: str = Field(default="direct", alias="{{extraction_method}}")
    connection_usage: Literal["CREATE", "REUSE"] = Field(
        default="REUSE",
        alias="{{connection_usage}}",
    )
