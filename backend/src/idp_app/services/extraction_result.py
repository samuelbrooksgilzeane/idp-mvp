from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from idp_app.services.document_models import (
    DocumentRecord,
    ExtractedFieldRecord,
    ExtractionRunRecord,
    InvoiceCandidateRecord,
    InvoiceLineCandidateRecord,
)
from idp_app.services.schema_models import ExtractField, SchemaRecord


def flatten_result(
    run: ExtractionRunRecord,
    schema: SchemaRecord,
    ai_result: dict[str, Any],
) -> list[ExtractedFieldRecord]:
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
    records: list[ExtractedFieldRecord] = []
    for name, definition in schema.ai_extract_schema.items():
        _walk(run, name, definition, response.get(name), citation_index, records)
    return records


def _walk(
    run: ExtractionRunRecord,
    path: str,
    definition: ExtractField,
    payload: object,
    citation_index: dict[int, dict[str, Any]],
    records: list[ExtractedFieldRecord],
) -> None:
    """Emit one row per scalar leaf, indexing repeated fields as `line_items[0].amount`.

    An absent or empty repeated field emits no rows; it is never treated as a zero-length
    result that could satisfy a calculation.
    """
    if definition.type == "array" and definition.items is not None:
        if isinstance(payload, list):
            for index, element in enumerate(payload):
                _walk(
                    run, f"{path}[{index}]", definition.items, element, citation_index, records
                )
        return
    if definition.type == "object" and definition.properties is not None:
        element = payload if isinstance(payload, dict) else {}
        for name, child in definition.properties.items():
            _walk(run, f"{path}.{name}", child, element.get(name), citation_index, records)
        return
    records.append(_flatten_scalar(run, path, definition, payload, citation_index))


# Invoice leaves are stated at the top level by a single-invoice contract, and under
# `invoices[i].` by a contract that admits several invoices per document. Both project into
# the same typed rows, distinguished by invoice_index.
INVOICE_PREFIX = re.compile(r"^invoices\[(\d+)\]\.(.+)$")
PROJECTED_LEAVES = frozenset(
    {"invoice_number", "invoice_date", "seller_name", "subtotal", "discount", "tax",
     "total", "currency"}
)
# `line_items[0].amount`, or `invoices[2].line_items[0].amount` when invoices repeat.
LINE_ITEM_PATH = re.compile(r"^(?:invoices\[(\d+)\]\.)?line_items\[(\d+)\]\.(.+)$")


def _invoice_leaves(
    fields: list[ExtractedFieldRecord],
) -> dict[int, dict[str, ExtractedFieldRecord]]:
    """Group the invoice-level leaves by the invoice they belong to.

    A schema that states its invoice fields somewhere this projection does not recognise
    contributes no group, so it is captured in the extracted fields and left unprojected
    rather than written as a row of nulls.
    """
    grouped: dict[int, dict[str, ExtractedFieldRecord]] = {}
    for field in fields:
        match = INVOICE_PREFIX.match(field.field_path)
        index = int(match.group(1)) if match else 0
        leaf = match.group(2) if match else field.field_path
        if leaf in PROJECTED_LEAVES:
            grouped.setdefault(index, {})[leaf] = field
    return grouped


def build_invoice_candidates(
    run: ExtractionRunRecord,
    document: DocumentRecord,
    fields: list[ExtractedFieldRecord],
) -> list[InvoiceCandidateRecord]:
    """Project each invoice the document states into its own typed candidate row."""
    return [
        InvoiceCandidateRecord(
            case_id=document.case_id,
            document_id=document.document_id,
            source_path=document.source_path,
            template_id=document.template_id,
            invoice_number=_string_value(leaves.get("invoice_number")),
            invoice_date=_date_value(leaves.get("invoice_date")),
            seller_name=_string_value(leaves.get("seller_name")),
            subtotal=_decimal_value(leaves.get("subtotal")),
            discount_amount=_decimal_value(leaves.get("discount")),
            tax_amount=_decimal_value(leaves.get("tax")),
            total_amount=_decimal_value(leaves.get("total")),
            currency=_string_value(leaves.get("currency")),
            extraction_run_id=run.extraction_run_id,
            schema_version=run.schema_version,
            invoice_index=index,
        )
        for index, leaves in sorted(_invoice_leaves(fields).items())
    ]


def build_invoice_line_candidates(
    run: ExtractionRunRecord, fields: list[ExtractedFieldRecord]
) -> list[InvoiceLineCandidateRecord]:
    """Project the repeated line leaves into typed rows.

    Only lines the model actually returned produce rows, so an invoice with no line table
    yields none rather than a zero-valued row that could be mistaken for a real line.
    """
    grouped: dict[tuple[int, int], dict[str, ExtractedFieldRecord]] = {}
    for field in fields:
        match = LINE_ITEM_PATH.match(field.field_path)
        if match is None:
            continue
        invoice_index = int(match.group(1)) if match.group(1) is not None else 0
        grouped.setdefault((invoice_index, int(match.group(2))), {})[match.group(3)] = field
    return [
        InvoiceLineCandidateRecord(
            extraction_run_id=run.extraction_run_id,
            document_id=run.document_id,
            # One-based within its own invoice; the matching evidence leaf is at
            # line_items[line_number - 1] under that invoice.
            line_number=line + 1,
            description=_string_value(leaves.get("description")),
            quantity=_decimal_value(leaves.get("quantity"), "0.0001"),
            unit_price=_decimal_value(leaves.get("unit_price")),
            tax=_decimal_value(leaves.get("tax")),
            amount=_decimal_value(leaves.get("amount")),
            invoice_index=invoice_index,
        )
        for (invoice_index, line), leaves in sorted(grouped.items())
    ]


def _flatten_scalar(
    run: ExtractionRunRecord,
    path: str,
    definition: ExtractField,
    payload: object,
    citation_index: dict[int, dict[str, Any]],
) -> ExtractedFieldRecord:
    errors: list[str] = []
    if payload is None:
        payload = {"value": None}
        errors.append("Field is absent from the ai_extract response.")
    if not isinstance(payload, dict):
        payload = {"value": None}
        errors.append("Field result is not a scalar field object.")

    value = payload.get("value")
    raw_ids: object = payload.get("citation_ids", [])
    citation_ids = (
        [item for item in raw_ids if isinstance(item, int)]
        if isinstance(raw_ids, list)
        else []
    )
    if raw_ids is not None and not isinstance(raw_ids, list):
        errors.append("citation_ids is not an array.")
    resolved = [
        citation_index[citation_id]
        for citation_id in citation_ids
        if citation_id in citation_index
    ]
    missing = [citation_id for citation_id in citation_ids if citation_id not in citation_index]
    if missing:
        errors.append("Missing citation metadata for IDs: " + ", ".join(map(str, missing)))

    confidence_raw = payload.get("confidence_score")
    confidence: float | None = None
    if isinstance(confidence_raw, int | float) and not isinstance(confidence_raw, bool):
        candidate = float(confidence_raw)
        if 0 <= candidate <= 1:
            confidence = candidate
        else:
            errors.append("confidence_score is outside the range 0 to 1.")
    elif confidence_raw is not None:
        errors.append("confidence_score is not numeric.")

    field_error = payload.get("error_message")
    if isinstance(field_error, str) and field_error:
        errors.append(field_error[:500])
    return ExtractedFieldRecord(
        extraction_run_id=run.extraction_run_id,
        document_id=run.document_id,
        field_path=path,
        field_type=definition.type,
        value=value,
        value_string=_value_string(value),
        confidence_score=confidence,
        citation_ids=citation_ids,
        citations=resolved,
        extraction_error=" ".join(errors) or None,
    )


def _value_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _string_value(field: ExtractedFieldRecord | None) -> str | None:
    if field is None or field.value is None:
        return None
    return str(field.value)


# Unambiguous, four-digit-year date forms. Named-month formats cannot be confused
# with day/month ordering, so they are safe to type explicitly; ambiguous numeric
# forms such as dd/mm/yyyy are intentionally excluded and remain null. This list must
# stay consistent with databricks_etl/src/extract_document.py.
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


def _date_value(field: ExtractedFieldRecord | None) -> date | None:
    value = _string_value(field)
    if value is None:
        return None
    text = value.strip()
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


def _decimal_value(
    field: ExtractedFieldRecord | None, exponent: str = "0.01"
) -> Decimal | None:
    if field is None or field.value is None or isinstance(field.value, bool):
        return None
    try:
        return Decimal(str(field.value)).quantize(Decimal(exponent))
    except (InvalidOperation, ValueError):
        return None
