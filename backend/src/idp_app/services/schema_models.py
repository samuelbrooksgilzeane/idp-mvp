from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,149}$")
# A rule may target a top-level field ("total"), a repeated element ("line_items[0].amount")
# or every element of a repeated field ("line_items[*].amount"). Repeated forms are accepted
# now so the rule engine needs no redesign when nested extraction lands.
_SEGMENT = r"[A-Za-z_][A-Za-z0-9_]{0,149}(?:\[(?:\*|\d+)\])?"
RULE_PATH = re.compile(rf"^{_SEGMENT}(?:\.{_SEGMENT})*$")


def rule_path_root(path: str) -> str:
    """Return the top-level extraction field that a rule path targets."""
    return re.split(r"[.\[]", path, maxsplit=1)[0]


SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean", "enum"})
MAX_SCHEMA_LEAVES = 256
MAX_SCHEMA_DEPTH = 12


def _normalise_numbers(value: object) -> object:
    """Render an integral float as an integer.

    The manifest is hashed independently by the backend, the registration task and the
    extraction task. JSON does not distinguish 0 from 0.0, but typed loading can, so the
    three implementations must agree on one representation or their hashes diverge.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _normalise_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_numbers(item) for item in value]
    return value


class ExtractField(BaseModel):
    """One node of the extraction contract.

    Scalars are leaves. `object` and `array` describe repeated or nested structures, matching the
    `ai_extract` schema shape. `items` and `properties` are optional so manifests registered before
    nesting existed keep an identical canonical form, and therefore an identical hash.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["string", "integer", "number", "boolean", "enum", "object", "array"]
    description: str = Field(min_length=1, max_length=1000)
    labels: list[str] | None = None
    items: ExtractField | None = None
    properties: dict[str, ExtractField] | None = None

    @model_validator(mode="after")
    def validate_field_shape(self) -> ExtractField:
        if self.type == "enum":
            if not self.labels:
                raise ValueError("enum fields require labels")
            if len(self.labels) > 500:
                raise ValueError("enum fields support at most 500 labels")
        elif self.labels is not None:
            raise ValueError("labels are only valid for enum fields")

        if self.type == "array":
            if self.items is None:
                raise ValueError("array fields require items")
        elif self.items is not None:
            raise ValueError("items are only valid for array fields")

        if self.type == "object":
            if not self.properties:
                raise ValueError("object fields require properties")
            invalid = [name for name in self.properties if not FIELD_NAME.fullmatch(name)]
            if invalid:
                raise ValueError(f"invalid extraction field name: {invalid[0]}")
        elif self.properties is not None:
            raise ValueError("properties are only valid for object fields")
        return self


def schema_leaves(
    schema: dict[str, ExtractField],
) -> list[tuple[str, ExtractField]]:
    """Every scalar leaf of the contract, keyed by its wildcard path.

    A top-level scalar keeps its bare name (`total`); a leaf inside a repeated field uses the
    wildcard form (`line_items[*].amount`), which is the same convention rule paths use.
    """
    leaves: list[tuple[str, ExtractField]] = []
    for name, field in schema.items():
        _collect_leaves(name, field, leaves, 1)
    return leaves


def _collect_leaves(
    path: str, field: ExtractField, leaves: list[tuple[str, ExtractField]], depth: int
) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise ValueError(f"extraction schema nests deeper than {MAX_SCHEMA_DEPTH} levels")
    if field.type == "array" and field.items is not None:
        _collect_leaves(f"{path}[*]", field.items, leaves, depth + 1)
    elif field.type == "object" and field.properties is not None:
        for name, child in field.properties.items():
            _collect_leaves(f"{path}.{name}", child, leaves, depth + 1)
    else:
        leaves.append((path, field))


def policy_path(instance_path: str) -> str:
    """Map an extracted instance path onto the wildcard path its policy is registered under."""
    return re.sub(r"\[\d+\]", "[*]", instance_path)


class FieldPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required: bool
    confidence_threshold: float = Field(ge=0, le=1)
    citation_required: bool
    risk_tier: Literal["low", "medium", "high"]
    # Optional declared meaning behind a raw string field, so semantic casting is validated from
    # the registered contract rather than hardcoded per use case. Optional keeps existing
    # manifests byte-identical under `canonical_json`.
    semantic_type: Literal["date", "currency_code"] | None = None


class RuleTerm(BaseModel):
    """One signed operand of an arithmetic reconciliation."""

    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(min_length=1, max_length=200)
    sign: Literal["+", "-"] = "+"
    # When set, the term folds every instance matching a wildcard path rather than reading one
    # value, so line-item totals need no new rule type.
    aggregate: Literal["sum"] | None = None


class DocumentRule(BaseModel):
    """A declarative business rule.

    Every parameter beyond the original contract is optional and defaults to None so that
    `SchemaManifest.canonical_json` (which excludes null values) keeps already-registered
    manifests byte-identical, preserving their immutable `schema_hash`.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    rule_type: Literal[
        "arithmetic_reconciliation",
        "required_fields",
        "allowed_values",
        "range",
        "format",
        "comparison",
    ]
    description: str = Field(min_length=1, max_length=1000)
    field_paths: list[str] = Field(min_length=1)
    tolerance: float | None = Field(default=None, ge=0)
    severity: Literal["INFO", "WARNING", "BLOCKING"] | None = None
    terms: list[RuleTerm] | None = Field(default=None, min_length=1)
    target: str | None = None
    allowed_values: list[str] | None = Field(default=None, min_length=1)
    minimum: float | None = None
    maximum: float | None = None
    pattern: str | None = Field(default=None, min_length=1, max_length=500)
    comparator: Literal["lt", "le", "gt", "ge", "eq", "ne"] | None = None
    compare_to: str | None = None


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
        leaves = schema_leaves(self.ai_extract_schema)
        if len(leaves) > MAX_SCHEMA_LEAVES:
            raise ValueError(f"extraction schema declares more than {MAX_SCHEMA_LEAVES} leaves")
        schema_fields = {path for path, _ in leaves}
        if set(self.field_policies) != schema_fields:
            raise ValueError("field_policies must define every extraction leaf exactly once")
        rule_ids: set[str] = set()
        for rule in self.document_rules:
            if rule.rule_id in rule_ids:
                raise ValueError(f"duplicate document rule: {rule.rule_id}")
            rule_ids.add(rule.rule_id)
            self._validate_rule(rule, schema_fields)
        return self

    @staticmethod
    def _validate_rule(rule: DocumentRule, schema_fields: set[str]) -> None:
        """Reject a malformed rule at registration so the schema hash covers valid config only."""
        referenced = list(rule.field_paths)
        if rule.terms:
            referenced.extend(term.field_path for term in rule.terms)
        if rule.target:
            referenced.append(rule.target)
        if rule.compare_to:
            referenced.append(rule.compare_to)
        for path in referenced:
            if RULE_PATH.fullmatch(path) is None:
                raise ValueError(f"document rule references an invalid field path: {path}")
            if policy_path(path) not in schema_fields:
                raise ValueError(f"document rule references unknown field: {path}")

        if rule.rule_type == "arithmetic_reconciliation":
            if rule.tolerance is None:
                raise ValueError("arithmetic reconciliation rules require a tolerance")
            if rule.terms is not None and rule.target is None:
                raise ValueError("arithmetic reconciliation terms require a target")
        elif rule.rule_type == "allowed_values":
            if not rule.allowed_values:
                raise ValueError("allowed_values rules require allowed_values")
        elif rule.rule_type == "range":
            if rule.minimum is None and rule.maximum is None:
                raise ValueError("range rules require a minimum or a maximum")
            if (
                rule.minimum is not None
                and rule.maximum is not None
                and rule.minimum > rule.maximum
            ):
                raise ValueError("range rules require minimum <= maximum")
        elif rule.rule_type == "format":
            if not rule.pattern:
                raise ValueError("format rules require a pattern")
            try:
                re.compile(rule.pattern)
            except re.error as error:
                raise ValueError(f"format rule pattern is invalid: {error}") from error
        elif rule.rule_type == "comparison":
            if rule.comparator is None or rule.compare_to is None:
                raise ValueError("comparison rules require a comparator and compare_to")

    def canonical_json(self) -> str:
        return json.dumps(
            _normalise_numbers(self.model_dump(mode="json", exclude_none=True)),
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
