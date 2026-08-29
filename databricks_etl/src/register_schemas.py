"""Register source-controlled extraction schemas without mutating existing versions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SCHEMA_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
FIELD_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,149}$")
FIELD_TYPES = {"string", "integer", "number", "boolean", "enum"}


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
    if parameters.manifest_path.name != "invoice_v1.json":
        raise ValueError("Only the source-controlled invoice_v1 manifest may be registered")


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
    if not isinstance(fields, dict) or not 1 <= len(fields) <= 256:
        raise ValueError("ai_extract_schema must contain between 1 and 256 fields")
    if set(fields) != set(policies):
        raise ValueError("field_policies must define every extraction field exactly once")
    for name, field in fields.items():
        if FIELD_IDENTIFIER.fullmatch(name) is None:
            raise ValueError(f"Invalid extraction field name: {name}")
        if not isinstance(field, dict) or field.get("type") not in FIELD_TYPES:
            raise ValueError(f"Invalid extraction field definition: {name}")
        if not isinstance(field.get("description"), str) or not field["description"]:
            raise ValueError(f"Extraction field description is required: {name}")
    return manifest


def canonical_json(value: object) -> str:
    return json.dumps(
        _without_nulls(value),
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
