from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,149}$")


class ExtractField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "integer", "number", "boolean", "enum"]
    description: str = Field(min_length=1, max_length=1000)
    labels: list[str] | None = None

    @model_validator(mode="after")
    def validate_enum_labels(self) -> ExtractField:
        if self.type == "enum":
            if not self.labels:
                raise ValueError("enum fields require labels")
            if len(self.labels) > 500:
                raise ValueError("enum fields support at most 500 labels")
        elif self.labels is not None:
            raise ValueError("labels are only valid for enum fields")
        return self


class FieldPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    confidence_threshold: float = Field(ge=0, le=1)
    citation_required: bool
    risk_tier: Literal["low", "medium", "high"]


class DocumentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    rule_type: Literal["arithmetic_reconciliation", "required_fields"]
    description: str = Field(min_length=1, max_length=1000)
    field_paths: list[str] = Field(min_length=1)
    tolerance: float | None = Field(default=None, ge=0)


class SchemaManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    schema_version: int = Field(ge=1)
    display_name: str = Field(min_length=1, max_length=200)
    use_case: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    status: Literal["PRODUCTION"]
    instructions: str = Field(min_length=1, max_length=20000)
    ai_extract_schema: dict[str, ExtractField] = Field(min_length=1, max_length=256)
    field_policies: dict[str, FieldPolicy]
    document_rules: list[DocumentRule]

    @model_validator(mode="after")
    def validate_field_contract(self) -> SchemaManifest:
        invalid_names = [name for name in self.ai_extract_schema if not FIELD_NAME.fullmatch(name)]
        if invalid_names:
            raise ValueError(f"invalid extraction field name: {invalid_names[0]}")
        schema_fields = set(self.ai_extract_schema)
        if set(self.field_policies) != schema_fields:
            raise ValueError("field_policies must define every extraction field exactly once")
        for rule in self.document_rules:
            unknown = set(rule.field_paths) - schema_fields
            if unknown:
                raise ValueError(f"document rule references unknown field: {sorted(unknown)[0]}")
            if rule.rule_type == "arithmetic_reconciliation" and rule.tolerance is None:
                raise ValueError("arithmetic reconciliation rules require a tolerance")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def schema_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @property
    def ai_extract_schema_json(self) -> str:
        fields = {
            name: field.model_dump(mode="json", exclude_none=True)
            for name, field in self.ai_extract_schema.items()
        }
        return json.dumps(
            fields,
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class SchemaRecord:
    schema_id: str
    schema_version: int
    display_name: str
    use_case: str
    ai_extract_schema: dict[str, ExtractField]
    instructions: str
    field_policies: dict[str, FieldPolicy]
    document_rules: list[DocumentRule]
    schema_hash: str
    status: str
    created_by: str
    created_at: datetime
