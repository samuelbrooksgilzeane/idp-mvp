"""Databricks Job task for one immutable document extraction attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SCHEMA_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,99}$")
UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
EXTRACTOR_VERSION = "2.1"
# Invoice leaves are stated at the top level by a single-invoice contract, and under
# `invoices[i].` by a contract that admits several invoices per document. Both project into
# the same typed rows, distinguished by invoice_index.
INVOICE_PREFIX = re.compile(r"^invoices\[(\d+)\]\.(.+)$")
# `line_items[0].amount`, or `invoices[2].line_items[0].amount` when invoices repeat.
LINE_ITEM_PATH = re.compile(r"^(?:invoices\[(\d+)\]\.)?line_items\[(\d+)\]\.(.+)$")
# The leaves this typed projection reads. A schema that states none of them is a shape the
# invoice candidate tables cannot describe.
PROJECTED_LEAVES = frozenset(
    {"invoice_number", "invoice_date", "seller_name", "subtotal", "discount", "tax",
     "total", "currency"}
)


@dataclass(frozen=True)
class Parameters:
    catalog: str
    project_schema: str
    table_prefix: str
    document_id: str
    extraction_run_id: str
    schema_id: str
    schema_version: int
    requested_by: str


def parse_arguments() -> Parameters:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--project-schema", required=True)
    parser.add_argument("--table-prefix", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--extraction-run-id", required=True)
    parser.add_argument("--schema-id", required=True)
    parser.add_argument("--schema-version", required=True, type=int)
    parser.add_argument("--requested-by", required=True)
    arguments = parser.parse_args()
    parameters = Parameters(
        catalog=arguments.catalog,
        project_schema=arguments.project_schema,
        table_prefix=arguments.table_prefix,
        document_id=arguments.document_id,
        extraction_run_id=arguments.extraction_run_id,
        schema_id=arguments.schema_id,
        schema_version=arguments.schema_version,
        requested_by=arguments.requested_by,
    )
    validate(parameters)
    return parameters


def validate(parameters: Parameters) -> None:
    if any(
        SIMPLE_IDENTIFIER.fullmatch(value) is None
        for value in (parameters.catalog, parameters.project_schema, parameters.table_prefix)
    ):
        raise ValueError("Databricks object configuration contains an invalid identifier")
    if UUID.fullmatch(parameters.document_id) is None:
        raise ValueError("document_id must be a UUID")
    if UUID.fullmatch(parameters.extraction_run_id) is None:
        raise ValueError("extraction_run_id must be a UUID")
    if SCHEMA_IDENTIFIER.fullmatch(parameters.schema_id) is None:
        raise ValueError("schema_id is invalid")
    if parameters.schema_version < 1:
        raise ValueError("schema_version must be positive")
    if not parameters.requested_by.strip() or len(parameters.requested_by) > 320:
        raise ValueError("requested_by is invalid")
    if any(ord(character) < 32 for character in parameters.requested_by):
        raise ValueError("requested_by contains control characters")


def qualified(parameters: Parameters, suffix: str) -> str:
    return (
        f"`{parameters.catalog}`.`{parameters.project_schema}`."
        f"`{parameters.table_prefix}_{suffix}`"
    )


def canonical_json(value: object) -> str:
    return json.dumps(
        _normalise_numbers(_without_nulls(value)),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
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


def main() -> None:
    parameters = parse_arguments()
    documents = qualified(parameters, "documents")
    parse_runs = qualified(parameters, "parsed_documents")
    schemas = qualified(parameters, "schema_registry")
    extraction_runs = qualified(parameters, "extraction_runs")
    extracted_fields = qualified(parameters, "extracted_fields")
    invoice_candidates = qualified(parameters, "invoice_candidates")
    invoice_line_candidates = qualified(parameters, "invoice_line_candidates")

    try:
        run = spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            SELECT parse_run_id, schema_hash, extractor_version, requested_by
            FROM {extraction_runs}
            WHERE extraction_run_id = :extraction_run_id
              AND document_id = :document_id
              AND schema_id = :schema_id
              AND schema_version = :schema_version
              AND status = 'RUNNING'
            LIMIT 1
            """,
            args={
                "extraction_run_id": parameters.extraction_run_id,
                "document_id": parameters.document_id,
                "schema_id": parameters.schema_id,
                "schema_version": parameters.schema_version,
            },
        ).first()
        if run is None:
            raise ValueError("The immutable extraction run does not match the trusted parameters")
        if run["extractor_version"] != EXTRACTOR_VERSION:
            raise ValueError("The extraction run does not use extractor version 2.1")
        if run["requested_by"] != parameters.requested_by:
            raise ValueError("The extraction requester does not match the immutable run")

        schema_row = spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            SELECT display_name, use_case, ai_extract_schema_json, instructions,
                   field_policy_json, document_rule_json, schema_hash, status, description
            FROM {schemas}
            WHERE schema_id = :schema_id AND schema_version = :schema_version
            LIMIT 1
            """,
            args={"schema_id": parameters.schema_id, "schema_version": parameters.schema_version},
        ).first()
        if schema_row is None or schema_row["status"] not in ("PRODUCTION", "PUBLISHED"):
            raise ValueError("The exact production or published extraction schema is not registered")
        schema_json = schema_row["ai_extract_schema_json"]
        manifest = {
            "schema_id": parameters.schema_id,
            "schema_version": parameters.schema_version,
            "display_name": schema_row["display_name"],
            "use_case": schema_row["use_case"],
            "status": schema_row["status"],
            "description": schema_row["description"],
            "instructions": schema_row["instructions"],
            "ai_extract_schema": json.loads(schema_json),
            "field_policies": json.loads(schema_row["field_policy_json"]),
            "document_rules": json.loads(schema_row["document_rule_json"]),
        }
        computed_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
        if computed_hash != schema_row["schema_hash"] or computed_hash != run["schema_hash"]:
            raise ValueError("The registered extraction schema hash does not match its content")

        document = spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            SELECT case_id, source_path, template_id
            FROM {documents}
            WHERE document_id = :document_id AND status = 'EXTRACTING'
            LIMIT 1
            """,
            args={"document_id": parameters.document_id},
        ).first()
        if document is None:
            raise ValueError("The document is not in the extraction state")

        latest_parse = spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            SELECT parse_run_id
            FROM {parse_runs}
            WHERE document_id = :document_id AND status = 'SUCCESS'
            ORDER BY completed_at DESC, parse_run_id DESC
            LIMIT 1
            """,
            args={"document_id": parameters.document_id},
        ).first()
        if latest_parse is None or latest_parse["parse_run_id"] != run["parse_run_id"]:
            raise ValueError("The extraction run does not reference the latest successful parse")

        result = spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            SELECT TO_JSON(
              ai_extract(
                parsed,
                :schema_json,
                options => map(
                  'version', '2.1',
                  'mode', 'precision',
                  'enableCitations', 'true',
                  'enableConfidenceScores', 'true',
                  'instructions', :instructions
                )
              )
            ) AS result_json
            FROM {parse_runs}
            WHERE parse_run_id = :parse_run_id AND document_id = :document_id
              AND status = 'SUCCESS'
            LIMIT 1
            """,
            args={
                "schema_json": schema_json,
                "instructions": schema_row["instructions"],
                "parse_run_id": run["parse_run_id"],
                "document_id": parameters.document_id,
            },
        ).first()
        if result is None or result["result_json"] is None:
            raise RuntimeError("ai_extract returned no result")

        # The complete model contract is committed before any generic flattening occurs.
        spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            UPDATE {extraction_runs}
            SET ai_result = PARSE_JSON(:result_json)
            WHERE extraction_run_id = :extraction_run_id
              AND status = 'RUNNING' AND ai_result IS NULL
            """,
            args={
                "extraction_run_id": parameters.extraction_run_id,
                "result_json": result["result_json"],
            },
        )

        ai_result = json.loads(result["result_json"])
        returned_error = ai_result.get("error_message")
        if isinstance(returned_error, str) and returned_error:
            raise RuntimeError(returned_error[:500])
        fields = flatten_fields(
            parameters.extraction_run_id,
            parameters.document_id,
            manifest["ai_extract_schema"],
            ai_result,
        )
        write_fields(extracted_fields, fields)
        # The generic extracted-field rows above carry every schema shape. The typed candidate
        # projection below understands one invoice per document, so a schema that nests its
        # invoices is captured and left unprojected rather than written as a row of nulls that
        # would surface in the summary as a blank invoice.
        candidates = build_candidates(parameters, document, fields)
        if not candidates:
            print(
                "Typed invoice projection skipped: this schema states no invoice fields "
                "this projection recognises. The extracted fields are recorded in full."
            )
        else:
            print(f"Projecting {len(candidates)} invoice(s) stated by this document.")
            write_candidates(invoice_candidates, candidates)
            write_line_candidates(
                invoice_line_candidates, build_line_candidates(parameters, fields)
            )

        spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            UPDATE {extraction_runs}
            SET status = 'EXTRACTED', error_message = NULL,
                completed_at = CURRENT_TIMESTAMP()
            WHERE extraction_run_id = :extraction_run_id
              AND status = 'RUNNING' AND ai_result IS NOT NULL
            """,
            args={"extraction_run_id": parameters.extraction_run_id},
        )
        spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            UPDATE {documents}
            SET status = 'EXTRACTED', updated_at = CURRENT_TIMESTAMP()
            WHERE document_id = :document_id AND status = 'EXTRACTING'
            """,
            args={"document_id": parameters.document_id},
        )
    except Exception as error:
        spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            UPDATE {extraction_runs}
            SET error_message = :error_message, status = 'FAILED',
                completed_at = CURRENT_TIMESTAMP()
            WHERE extraction_run_id = :extraction_run_id AND status = 'RUNNING'
            """,
            args={
                "extraction_run_id": parameters.extraction_run_id,
                "error_message": (str(error) or "Document extraction failed in Databricks.")[:500],
            },
        )
        spark.sql(  # type: ignore[name-defined]  # noqa: F821
            f"""
            UPDATE {documents}
            SET status = 'EXTRACT_FAILED', updated_at = CURRENT_TIMESTAMP()
            WHERE document_id = :document_id AND status = 'EXTRACTING'
            """,
            args={"document_id": parameters.document_id},
        )
        raise


def flatten_fields(
    extraction_run_id: str,
    document_id: str,
    schema: dict[str, Any],
    ai_result: dict[str, Any],
) -> list[dict[str, Any]]:
    response = ai_result.get("response")
    if not isinstance(response, dict):
        raise ValueError("ai_extract response is missing its response object")
    metadata = ai_result.get("metadata")
    citations = metadata.get("citations", []) if isinstance(metadata, dict) else []
    citation_index = {
        item["id"]: item
        for item in citations
        if isinstance(item, dict) and isinstance(item.get("id"), int)
    }
    fields: list[dict[str, Any]] = []
    for name, definition in schema.items():
        _walk(extraction_run_id, document_id, name, definition, response.get(name),
              citation_index, fields)
    return fields


def _walk(
    extraction_run_id: str,
    document_id: str,
    path: str,
    definition: Any,
    payload: Any,
    citation_index: dict[int, Any],
    fields: list[dict[str, Any]],
) -> None:
    """Emit one row per scalar leaf, indexing repeated fields as `line_items[0].amount`.

    An absent or empty repeated field emits no rows; it is never treated as a zero-length
    result that could satisfy a calculation.
    """
    if not isinstance(definition, dict):
        raise ValueError(f"Registered field definition is invalid: {path}")
    field_type = definition.get("type")
    if field_type == "array":
        items = definition.get("items")
        if isinstance(payload, list) and isinstance(items, dict):
            for index, element in enumerate(payload):
                _walk(
                    extraction_run_id, document_id, f"{path}[{index}]", items, element,
                    citation_index, fields,
                )
        return
    if field_type == "object":
        properties = definition.get("properties")
        if isinstance(properties, dict):
            element = payload if isinstance(payload, dict) else {}
            for name, child in properties.items():
                _walk(
                    extraction_run_id, document_id, f"{path}.{name}", child, element.get(name),
                    citation_index, fields,
                )
        return
    fields.append(
        _flatten_scalar(extraction_run_id, document_id, path, definition, payload, citation_index)
    )


def _flatten_scalar(
    extraction_run_id: str,
    document_id: str,
    path: str,
    definition: dict[str, Any],
    payload: Any,
    citation_index: dict[int, Any],
) -> dict[str, Any]:
        errors: list[str] = []
        if not isinstance(payload, dict):
            payload = {"value": None}
            errors.append("Field is absent or is not a scalar field object.")
        raw_ids = payload.get("citation_ids", [])
        citation_ids = [item for item in raw_ids if isinstance(item, int)] if isinstance(raw_ids, list) else []
        resolved = [citation_index[item] for item in citation_ids if item in citation_index]
        missing = [item for item in citation_ids if item not in citation_index]
        if missing:
            errors.append("Missing citation metadata for IDs: " + ", ".join(map(str, missing)))
        confidence = payload.get("confidence_score")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = None
        elif not 0 <= float(confidence) <= 1:
            confidence = None
            errors.append("confidence_score is outside the range 0 to 1.")
        value = payload.get("value")
        return {
            "extraction_run_id": extraction_run_id,
            "document_id": document_id,
            "field_path": path,
            "field_type": definition.get("type"),
            "value": value,
            "value_string": value_string(value),
            "confidence_score": float(confidence) if confidence is not None else None,
            "citation_ids": citation_ids,
            "citations": resolved,
            "extraction_error": " ".join(errors) or None,
        }


def value_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def write_fields(table: str, fields: list[dict[str, Any]]) -> None:
    from pyspark.sql.types import (  # type: ignore[import-not-found]
        ArrayType,
        DoubleType,
        IntegerType,
        StringType,
        StructField,
        StructType,
    )

    rows = [
        (
            field["extraction_run_id"], field["document_id"], field["field_path"],
            field["field_type"], json.dumps(field["value"], separators=(",", ":")),
            field["value_string"], field["confidence_score"], field["citation_ids"],
            json.dumps(field["citations"], separators=(",", ":")), field["extraction_error"],
        )
        for field in fields
    ]
    schema = StructType(
        [
            StructField("extraction_run_id", StringType(), False),
            StructField("document_id", StringType(), False),
            StructField("field_path", StringType(), False),
            StructField("field_type", StringType(), False),
            StructField("value_json", StringType(), False),
            StructField("value_string", StringType(), True),
            StructField("confidence_score", DoubleType(), True),
            StructField("citation_ids", ArrayType(IntegerType()), False),
            StructField("citations_json", StringType(), False),
            StructField("extraction_error", StringType(), True),
        ]
    )
    spark.createDataFrame(rows, schema).createOrReplaceTempView(  # type: ignore[name-defined]  # noqa: F821
        "idp_extracted_fields_to_insert"
    )
    spark.sql(  # type: ignore[name-defined]  # noqa: F821
        f"""
        INSERT INTO {table}
        (extraction_run_id, document_id, field_path, field_type,
         value, value_string, confidence_score, citation_ids, citations, extraction_error)
        SELECT extraction_run_id, document_id, field_path, field_type,
               PARSE_JSON(value_json), value_string, confidence_score,
               citation_ids, PARSE_JSON(citations_json), extraction_error
        FROM idp_extracted_fields_to_insert
        """
    )


def invoice_leaves(fields: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Group the invoice-level leaves by the invoice they belong to.

    A schema that states its invoice fields somewhere this projection does not recognise
    contributes no group, so it is captured in the extracted fields and left unprojected
    rather than written as a row of nulls.
    """
    grouped: dict[int, dict[str, Any]] = {}
    for field in fields:
        match = INVOICE_PREFIX.match(field["field_path"])
        index = int(match.group(1)) if match else 0
        leaf = match.group(2) if match else field["field_path"]
        if leaf in PROJECTED_LEAVES:
            grouped.setdefault(index, {})[leaf] = field["value"]
    return grouped


def build_candidates(
    parameters: Parameters,
    document: Any,
    fields: list[dict[str, Any]],
) -> list[tuple[object, ...]]:
    """Project each invoice the document states into its own typed candidate row."""
    return [
        (
            document["case_id"], parameters.document_id, document["source_path"],
            document["template_id"], string_or_none(values.get("invoice_number")),
            parse_date(values.get("invoice_date")), string_or_none(values.get("seller_name")),
            parse_decimal(values.get("subtotal")), parse_decimal(values.get("discount")),
            parse_decimal(values.get("tax")), parse_decimal(values.get("total")),
            string_or_none(values.get("currency")), parameters.extraction_run_id,
            parameters.schema_version, index,
        )
        for index, values in sorted(invoice_leaves(fields).items())
    ]


def string_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


# Unambiguous, four-digit-year date forms. Named-month formats cannot be confused
# with day/month ordering, so they are safe to type explicitly; ambiguous numeric
# forms such as dd/mm/yyyy are intentionally excluded and remain null.
_DATE_FORMATS = (
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
)


def parse_date(value: object) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def parse_decimal(value: object, exponent: str = "0.01") -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal(exponent))
    except (InvalidOperation, ValueError):
        return None


def build_line_candidates(
    parameters: Parameters, fields: list[dict[str, Any]]
) -> list[tuple[object, ...]]:
    """Project the repeated line leaves into typed rows.

    Only lines the model actually returned produce rows, so an invoice with no line table
    yields none rather than a zero-valued row that could be mistaken for a real line.
    """
    grouped: dict[tuple[int, int], dict[str, Any]] = {}
    for field in fields:
        match = LINE_ITEM_PATH.match(field["field_path"])
        if match is None:
            continue
        invoice_index = int(match.group(1)) if match.group(1) is not None else 0
        grouped.setdefault((invoice_index, int(match.group(2))), {})[match.group(3)] = (
            field["value"]
        )
    return [
        (
            parameters.extraction_run_id,
            parameters.document_id,
            # One-based within its own invoice; the matching evidence leaf is at
            # line_items[line_number - 1] under that invoice.
            line + 1,
            string_or_none(leaves.get("description")),
            parse_decimal(leaves.get("quantity"), "0.0001"),
            parse_decimal(leaves.get("unit_price")),
            parse_decimal(leaves.get("tax")),
            parse_decimal(leaves.get("amount")),
            invoice_index,
        )
        for (invoice_index, line), leaves in sorted(grouped.items())
    ]


def write_line_candidates(table: str, lines: list[tuple[object, ...]]) -> None:
    if not lines:
        return
    from pyspark.sql.types import (  # type: ignore[import-not-found]
        DecimalType,
        IntegerType,
        StringType,
        StructField,
        StructType,
    )

    names = (
        "extraction_run_id", "document_id", "line_number", "description",
        "quantity", "unit_price", "tax", "amount", "invoice_index",
    )
    line_schema = StructType(
        [
            StructField("extraction_run_id", StringType(), False),
            StructField("document_id", StringType(), False),
            StructField("line_number", IntegerType(), False),
            StructField("description", StringType(), True),
            StructField("quantity", DecimalType(18, 4), True),
            StructField("unit_price", DecimalType(18, 2), True),
            StructField("tax", DecimalType(18, 2), True),
            StructField("amount", DecimalType(18, 2), True),
            StructField("invoice_index", IntegerType(), False),
        ]
    )
    spark.createDataFrame(lines, line_schema).createOrReplaceTempView(  # type: ignore[name-defined]  # noqa: F821
        "idp_invoice_lines_to_insert"
    )
    spark.sql(  # type: ignore[name-defined]  # noqa: F821
        f"INSERT INTO {table} SELECT {', '.join(names)} FROM idp_invoice_lines_to_insert"
    )


def write_candidates(table: str, candidates: list[tuple[object, ...]]) -> None:
    from pyspark.sql.types import (  # type: ignore[import-not-found]
        DateType,
        DecimalType,
        IntegerType,
        StringType,
        StructField,
        StructType,
    )

    names = (
        "case_id", "document_id", "source_path", "template_id", "invoice_number",
        "invoice_date", "seller_name", "subtotal", "discount_amount", "tax_amount",
        "total_amount", "currency", "extraction_run_id", "schema_version", "invoice_index",
    )
    types = (
        StringType(), StringType(), StringType(), StringType(), StringType(), DateType(),
        StringType(), DecimalType(18, 2), DecimalType(18, 2), DecimalType(18, 2),
        DecimalType(18, 2), StringType(), StringType(), IntegerType(), IntegerType(),
    )
    # A column is nullable when no invoice states a value for it.
    candidate_schema = StructType(
        [
            StructField(
                name, field_type, any(candidate[position] is None for candidate in candidates)
            )
            for position, (name, field_type) in enumerate(zip(names, types, strict=True))
        ]
    )
    spark.createDataFrame(candidates, candidate_schema).createOrReplaceTempView(  # type: ignore[name-defined]  # noqa: F821
        "idp_invoice_candidate_to_insert"
    )
    spark.sql(  # type: ignore[name-defined]  # noqa: F821
        f"INSERT INTO {table} SELECT {', '.join(names)} FROM idp_invoice_candidate_to_insert"
    )


if __name__ == "__main__":
    main()
