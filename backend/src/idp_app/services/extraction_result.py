from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from idp_app.services.document_models import (
    DocumentRecord,
    ExtractedFieldRecord,
    ExtractionRunRecord,
    InvoiceCandidateRecord,
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
    return [
        _flatten_scalar(run, path, definition, response.get(path), citation_index)
        for path, definition in schema.ai_extract_schema.items()
    ]


def build_invoice_candidate(
    run: ExtractionRunRecord,
    document: DocumentRecord,
    fields: list[ExtractedFieldRecord],
) -> InvoiceCandidateRecord:
    by_path = {field.field_path: field for field in fields}
    return InvoiceCandidateRecord(
        case_id=document.case_id,
        document_id=document.document_id,
        source_path=document.source_path,
        template_id=document.template_id,
        invoice_number=_string_value(by_path.get("invoice_number")),
        invoice_date=_date_value(by_path.get("invoice_date")),
        seller_name=_string_value(by_path.get("seller_name")),
        subtotal=_decimal_value(by_path.get("subtotal")),
        discount_amount=_decimal_value(by_path.get("discount")),
        tax_amount=_decimal_value(by_path.get("tax")),
        total_amount=_decimal_value(by_path.get("total")),
        currency=_string_value(by_path.get("currency")),
        extraction_run_id=run.extraction_run_id,
        schema_version=run.schema_version,
    )


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


def _decimal_value(field: ExtractedFieldRecord | None) -> Decimal | None:
    if field is None or field.value is None or isinstance(field.value, bool):
        return None
    try:
        return Decimal(str(field.value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
