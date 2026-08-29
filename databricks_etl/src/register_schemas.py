"""Register source-controlled extraction schemas without mutating existing versions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ALLOWED_MANIFESTS = frozenset(
    {"invoice_v1.json", "invoice_v2.json", "invoice_v3.json"}
)
SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SCHEMA_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
FIELD_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,149}$")
SCALAR_TYPES = {"string", "integer", "number", "boolean", "enum"}
FIELD_TYPES = SCALAR_TYPES | {"object", "array"}
MAX_SCHEMA_LEAVES = 256
MAX_SCHEMA_DEPTH = 12


@dataclass(frozen=True)
class Parameters:
    catalog: str
    project_schema: str
    table_prefix: str
    manifest_path: Path


def parse_arguments() -> Parameters:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--project-schema", required=True)
    parser.add_argument("--table-prefix", required=True)
    parser.add_argument("--manifest-path", required=True)
    arguments = parser.parse_args()
    parameters = Parameters(
        catalog=arguments.catalog,
        project_schema=arguments.project_schema,
        table_prefix=arguments.table_prefix,
        manifest_path=Path(arguments.manifest_path),
    )
    validate_parameters(parameters)
    return parameters


def validate_parameters(parameters: Parameters) -> None:
    identifiers = (
        parameters.catalog,
        parameters.project_schema,
        parameters.table_prefix,
    )
    if any(SIMPLE_IDENTIFIER.fullmatch(value) is None for value in identifiers):
        raise ValueError("Databricks object configuration contains an invalid identifier")
    if parameters.manifest_path.name not in ALLOWED_MANIFESTS:
        raise ValueError("Only source-controlled invoice manifests may be registered")


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_id",
        "schema_version",
        "display_name",
        "use_case",
        "status",
        "instructions",
        "ai_extract_schema",
        "field_policies",
        "document_rules",
    }
    if set(manifest) != required:
        raise ValueError("Schema manifest keys do not match the governed contract")
    if SCHEMA_IDENTIFIER.fullmatch(manifest["schema_id"]) is None:
        raise ValueError("schema_id is invalid")
    if SCHEMA_IDENTIFIER.fullmatch(manifest["use_case"]) is None:
        raise ValueError("use_case is invalid")
    if not isinstance(manifest["schema_version"], int) or manifest["schema_version"] < 1:
        raise ValueError("schema_version must be a positive integer")
    if manifest["status"] != "PRODUCTION":
        raise ValueError("Only production schemas may be registered")
    if not isinstance(manifest["instructions"], str) or not manifest["instructions"]:
        raise ValueError("instructions are required")
    fields = manifest["ai_extract_schema"]
    policies = manifest["field_policies"]
    if not isinstance(fields, dict) or not fields:
        raise ValueError("ai_extract_schema must declare at least one field")
    leaves: list[str] = []
    for name, field in fields.items():
        _validate_field(name, name, field, leaves, 1)
    if not 1 <= len(leaves) <= MAX_SCHEMA_LEAVES:
        raise ValueError(
            f"ai_extract_schema must contain between 1 and {MAX_SCHEMA_LEAVES} leaves"
        )
    if set(leaves) != set(policies):
        raise ValueError("field_policies must define every extraction leaf exactly once")
    return manifest


def _validate_field(
    name: str, path: str, field: object, leaves: list[str], depth: int
) -> None:
    """Recursively validate one contract node and collect its scalar leaf paths."""
    if depth > MAX_SCHEMA_DEPTH:
        raise ValueError(f"Extraction schema nests deeper than {MAX_SCHEMA_DEPTH} levels")
    if FIELD_IDENTIFIER.fullmatch(name) is None:
        raise ValueError(f"Invalid extraction field name: {name}")
    if not isinstance(field, dict) or field.get("type") not in FIELD_TYPES:
        raise ValueError(f"Invalid extraction field definition: {path}")
    if not isinstance(field.get("description"), str) or not field["description"]:
        raise ValueError(f"Extraction field description is required: {path}")

    field_type = field["type"]
    if field_type == "array":
        items = field.get("items")
        if not isinstance(items, dict):
            raise ValueError(f"Array field requires items: {path}")
        _validate_field(name, f"{path}[*]", items, leaves, depth + 1)
        return
    if field_type == "object":
        properties = field.get("properties")
        if not isinstance(properties, dict) or not properties:
            raise ValueError(f"Object field requires properties: {path}")
        for child_name, child in properties.items():
            _validate_field(child_name, f"{path}.{child_name}", child, leaves, depth + 1)
        return
    leaves.append(path)


def canonical_json(value: object) -> str:
    return json.dumps(
        _normalise_numbers(_without_nulls(value)),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def compact_json(value: object) -> str:
    return json.dumps(
        _without_nulls(value),
        ensure_ascii=False,
        separators=(",", ":"),
    )


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


def _without_nulls(value: object) -> object:
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value]
    return value


def qualified(parameters: Parameters) -> str:
    return (
        f"`{parameters.catalog}`.`{parameters.project_schema}`."
        f"`{parameters.table_prefix}_schema_registry`"
    )


def main() -> None:
    parameters = parse_arguments()
    manifest = load_manifest(parameters.manifest_path)
    schema_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    table = qualified(parameters)
    values = {
        "schema_id": manifest["schema_id"],
        "schema_version": manifest["schema_version"],
        "display_name": manifest["display_name"],
        "use_case": manifest["use_case"],
        "ai_extract_schema_json": compact_json(manifest["ai_extract_schema"]),
        "instructions": manifest["instructions"],
        "field_policy_json": canonical_json(manifest["field_policies"]),
        "document_rule_json": canonical_json(manifest["document_rules"]),
        "schema_hash": schema_hash,
        "status": manifest["status"],
    }
    spark.sql(  # type: ignore[name-defined]  # noqa: F821 - Databricks injects Spark.
        f"""
        MERGE INTO {table} AS target
        USING (
          SELECT
            :schema_id AS schema_id,
            :schema_version AS schema_version,
            :display_name AS display_name,
            :use_case AS use_case,
            :ai_extract_schema_json AS ai_extract_schema_json,
            :instructions AS instructions,
            :field_policy_json AS field_policy_json,
            :document_rule_json AS document_rule_json,
            :schema_hash AS schema_hash,
            :status AS status,
            current_user() AS created_by,
            current_timestamp() AS created_at
        ) AS source
        ON target.schema_id = source.schema_id
           AND target.schema_version = source.schema_version
        WHEN NOT MATCHED THEN INSERT *
        """,
        args=values,
    )
    registered = spark.sql(  # type: ignore[name-defined]  # noqa: F821
        f"""
        SELECT schema_hash
        FROM {table}
        WHERE schema_id = :schema_id AND schema_version = :schema_version
        LIMIT 1
        """,
        args={
            "schema_id": manifest["schema_id"],
            "schema_version": manifest["schema_version"],
        },
    ).first()
    if registered is None or registered["schema_hash"] != schema_hash:
        raise ValueError(
            "The registered schema version has different content; increment schema_version"
        )


if __name__ == "__main__":
    main()
