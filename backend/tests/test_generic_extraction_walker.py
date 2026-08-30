"""Tests for the schema-agnostic recursive extraction walker.

These exercise `walk_extraction` directly against deterministic, hand-built `ai_extract`
response shapes (no AI functions are called), matching the ten fixtures called for by the
generalized IDP plan: a flat form, single and repeated root records, three-level nesting,
sibling arrays, scalar arrays, null/empty results, maximum depth, over-limit schemas, and
retry idempotency.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.extraction_result import walk_extraction
from idp_app.services.schema_models import (
    MAX_SCHEMA_DEPTH,
    ExtractField,
    SchemaManifest,
    SchemaRecord,
    schema_leaves,
)

RUN_ID = "f5369a2d-aa62-47bd-b075-417b25e2b4eb"
DOCUMENT_ID = "ce584838-9345-4223-a035-21337274dce1"


def _run() -> ExtractionRunRecord:
    now = datetime.now(UTC)
    return ExtractionRunRecord(
        extraction_run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        parse_run_id="b580cfb4-e31c-49f4-a921-4d0e5ae634ab",
        schema_id="generic_test_schema",
        schema_version=1,
        schema_hash="hash",
        extractor_version="2.1",
        options={},
        ai_result=None,
        error_message=None,
        status="EXTRACTED",
        requested_by="test@example.com",
        job_run_id=None,
        started_at=now,
        completed_at=now,
    )


def _schema(fields: dict[str, ExtractField]) -> SchemaRecord:
    return SchemaRecord(
        schema_id="generic_test_schema",
        schema_version=1,
        display_name="Generic test schema",
        use_case="generic",
        ai_extract_schema=fields,
        instructions="Test schema.",
        field_policies={},
        document_rules=[],
        schema_hash="hash",
        status="DRAFT",
        created_by="tester",
        created_at=datetime.now(UTC),
    )


def _scalar(value: object, confidence: float = 0.9, citation_ids: list[int] | None = None) -> dict:
    return {"value": value, "confidence_score": confidence, "citation_ids": citation_ids or []}


def _ai_result(response: dict, citations: list[dict] | None = None) -> dict:
    return {
        "response": response,
        "error_message": None,
        "metadata": {"citations": citations or []},
    }


def _by_instance_path(fields, path):
    matches = [field for field in fields if field.instance_path == path]
    assert len(matches) == 1, f"expected exactly one field at {path!r}, got {matches}"
    return matches[0]


# 1. Flat tax form: a single root object with a name and an SSN, no repeated records.
def test_flat_tax_form_single_record() -> None:
    fields = {
        "full_name": ExtractField(type="string", description="Filer's full legal name."),
        "ssn": ExtractField(type="string", description="Filer's Social Security Number."),
    }
    ai_result = _ai_result(
        {"full_name": _scalar("Jordan Rivera"), "ssn": _scalar("123-45-6789")}
    )
    records, extracted = walk_extraction(_run(), _schema(fields), ai_result)

    assert [record.instance_path for record in records] == ["$"]
    assert records[0].parent_record_id is None
    assert {field.instance_path: field.value for field in extracted} == {
        "full_name": "Jordan Rivera",
        "ssn": "123-45-6789",
    }
    assert all(field.record_id == records[0].record_id for field in extracted)


# 2. One invoice returned inside invoices[].
def test_single_repeated_record() -> None:
    fields = {
        "invoices": ExtractField(
            type="array",
            description="Invoices.",
            items=ExtractField(
                type="object",
                description="One invoice.",
                properties={
                    "invoice_number": ExtractField(type="string", description="Number."),
                },
            ),
        )
    }
    ai_result = _ai_result({"invoices": [{"invoice_number": _scalar("INV-1")}]})
    records, extracted = walk_extraction(_run(), _schema(fields), ai_result)

    root = _by_instance_path(records, "$")
    invoice = _by_instance_path(records, "invoices[0]")
    assert invoice.parent_record_id == root.record_id
    assert invoice.ordinal == 0
    field = _by_instance_path(extracted, "invoices[0].invoice_number")
    assert field.value == "INV-1"
    assert field.record_id == invoice.record_id
    assert field.field_name == "invoice_number"


# 3. Multiple invoices in one document.
def test_multiple_repeated_records_keep_own_ordinal() -> None:
    fields = {
        "invoices": ExtractField(
            type="array",
            description="Invoices.",
            items=ExtractField(
                type="object",
                description="One invoice.",
                properties={
                    "invoice_number": ExtractField(type="string", description="Number."),
                },
            ),
        )
    }
    ai_result = _ai_result(
        {
            "invoices": [
                {"invoice_number": _scalar("INV-1")},
                {"invoice_number": _scalar("INV-2")},
                {"invoice_number": _scalar("INV-3")},
            ]
        }
    )
    records, extracted = walk_extraction(_run(), _schema(fields), ai_result)

    invoice_records = sorted(
        (record for record in records if record.schema_path == "invoices[]"),
        key=lambda record: record.ordinal,
    )
    assert [record.ordinal for record in invoice_records] == [0, 1, 2]
    assert len(set(record.record_id for record in invoice_records)) == 3
    numbers = {
        field.instance_path: field.value
        for field in extracted
        if field.field_name == "invoice_number"
    }
    assert numbers == {
        "invoices[0].invoice_number": "INV-1",
        "invoices[1].invoice_number": "INV-2",
        "invoices[2].invoice_number": "INV-3",
    }


# 4. Invoice -> line_items -> taxes: three levels of nesting.
def _nested_invoice_schema() -> dict[str, ExtractField]:
    tax_item = ExtractField(
        type="object",
        description="One tax line.",
        properties={"amount": ExtractField(type="number", description="Tax amount.")},
    )
    line_item = ExtractField(
        type="object",
        description="One billed line.",
        properties={
            "description": ExtractField(type="string", description="Line description."),
            "taxes": ExtractField(type="array", description="Taxes on the line.", items=tax_item),
        },
    )
    invoice = ExtractField(
        type="object",
        description="One invoice.",
        properties={
            "invoice_number": ExtractField(type="string", description="Number."),
            "line_items": ExtractField(
                type="array", description="Billed lines.", items=line_item
            ),
        },
    )
    return {"invoices": ExtractField(type="array", description="Invoices.", items=invoice)}


def test_three_level_nesting_invoice_line_items_taxes() -> None:
    fields = _nested_invoice_schema()
    ai_result = _ai_result(
        {
            "invoices": [
                {
                    "invoice_number": _scalar("INV-1"),
                    "line_items": [
                        {
                            "description": _scalar("Widget"),
                            "taxes": [{"amount": _scalar(1.5)}, {"amount": _scalar(0.5)}],
                        }
                    ],
                }
            ]
        }
    )
    records, extracted = walk_extraction(_run(), _schema(fields), ai_result)

    invoice = _by_instance_path(records, "invoices[0]")
    line = _by_instance_path(records, "invoices[0].line_items[0]")
    tax_0 = _by_instance_path(records, "invoices[0].line_items[0].taxes[0]")
    tax_1 = _by_instance_path(records, "invoices[0].line_items[0].taxes[1]")
    assert line.parent_record_id == invoice.record_id
    assert tax_0.parent_record_id == line.record_id
    assert tax_1.parent_record_id == line.record_id
    assert tax_0.schema_path == "invoices[].line_items[].taxes[]"
    amounts = sorted(
        field.value for field in extracted if field.field_name == "amount"
    )
    assert amounts == [0.5, 1.5]


# 5. Two sibling arrays must never be cross-joined.
def test_sibling_arrays_are_not_cartesian_joined() -> None:
    fields = {
        "line_items": ExtractField(
            type="array",
            description="Billed lines.",
            items=ExtractField(
                type="object",
                description="Line.",
                properties={"description": ExtractField(type="string", description="Desc.")},
            ),
        ),
        "notes": ExtractField(
            type="array",
            description="Free-text notes.",
            items=ExtractField(
                type="object",
                description="Note.",
                properties={"text": ExtractField(type="string", description="Text.")},
            ),
        ),
    }
    ai_result = _ai_result(
        {
            "line_items": [{"description": _scalar("A")}, {"description": _scalar("B")}],
            "notes": [
                {"text": _scalar("n1")},
                {"text": _scalar("n2")},
                {"text": _scalar("n3")},
            ],
        }
    )
    records, _ = walk_extraction(_run(), _schema(fields), ai_result)

    line_records = [r for r in records if r.schema_path == "line_items[]"]
    note_records = [r for r in records if r.schema_path == "notes[]"]
    # 2 lines + 3 notes + root = 6, never 2*3 combinations.
    assert len(line_records) == 2
    assert len(note_records) == 3
    assert len(records) == 1 + 2 + 3


# 6. An array of plain scalar values (no wrapping object).
def test_array_of_scalar_values() -> None:
    fields = {
        "tags": ExtractField(
            type="array",
            description="Free-text tags.",
            items=ExtractField(type="string", description="One tag."),
        )
    }
    ai_result = _ai_result({"tags": [_scalar("urgent"), _scalar("reviewed")]})
    records, extracted = walk_extraction(_run(), _schema(fields), ai_result)

    # No per-item record is created for a scalar array; both values attach to the root.
    assert [record.instance_path for record in records] == ["$"]
    values = sorted(field.value for field in extracted if field.schema_path == "tags[]")
    assert values == ["reviewed", "urgent"]


# 7. Empty arrays and null scalar fields are valid, not failures.
def test_empty_array_and_null_field_are_valid() -> None:
    fields = {
        "line_items": ExtractField(
            type="array",
            description="Billed lines.",
            items=ExtractField(
                type="object",
                description="Line.",
                properties={"description": ExtractField(type="string", description="Desc.")},
            ),
        ),
        "seller_name": ExtractField(type="string", description="Seller."),
    }
    ai_result = _ai_result({"line_items": [], "seller_name": _scalar(None)})
    records, extracted = walk_extraction(_run(), _schema(fields), ai_result)

    assert [record.instance_path for record in records] == ["$"]
    seller = _by_instance_path(extracted, "seller_name")
    assert seller.value is None
    # No line_items rows at all -- absent, not an error state.
    assert not any(field.schema_path.startswith("line_items") for field in extracted)

    # A field entirely missing from the response is likewise recorded, not raised.
    ai_result_missing = _ai_result({"line_items": []})
    _, extracted_missing = walk_extraction(_run(), _schema(fields), ai_result_missing)
    missing_seller = _by_instance_path(extracted_missing, "seller_name")
    assert missing_seller.value is None
    assert missing_seller.validation_message is not None


# 8. Maximum permitted nesting depth (12 levels) is walked without error.
def test_maximum_permitted_depth_is_walked() -> None:
    leaf = ExtractField(type="string", description="Leaf value.")
    node = leaf
    # Build 12 nested single-key objects (a chain), the deepest permitted shape.
    for level in range(MAX_SCHEMA_DEPTH - 1, 0, -1):
        node = ExtractField(
            type="object", description=f"Level {level}.", properties={"child": node}
        )
    fields = {"root": node}
    # Confirm the schema itself is within the declared limits before walking it.
    schema_leaves(fields)

    def _nest(depth: int) -> dict:
        if depth == 0:
            return _scalar("bottom")
        return {"child": _nest(depth - 1)}

    ai_result = _ai_result({"root": _nest(MAX_SCHEMA_DEPTH - 1)})
    records, extracted = walk_extraction(_run(), _schema(fields), ai_result)
    assert len(records) == MAX_SCHEMA_DEPTH  # root + one object record per nested level
    [bottom] = [field for field in extracted if field.field_name == "child"]
    assert bottom.value == "bottom"


# 9. A schema exceeding the depth or field-count limit is rejected before extraction.
def test_schema_exceeding_depth_limit_is_rejected() -> None:
    leaf = ExtractField(type="string", description="Leaf value.")
    node = leaf
    for level in range(MAX_SCHEMA_DEPTH + 3, 0, -1):
        node = ExtractField(
            type="object", description=f"Level {level}.", properties={"child": node}
        )
    with pytest.raises(ValueError, match="nests deeper"):
        schema_leaves({"root": node})


def test_schema_exceeding_leaf_limit_is_rejected() -> None:
    properties = {
        f"field_{index}": ExtractField(type="string", description="Leaf.")
        for index in range(300)
    }
    with pytest.raises(ValidationError):
        SchemaManifest(
            schema_id="too_many_leaves",
            schema_version=1,
            display_name="Too many leaves",
            use_case="generic",
            status="PRODUCTION",
            instructions="Test.",
            ai_extract_schema=properties,
            field_policies={
                name: {
                    "required": False,
                    "confidence_threshold": 0.5,
                    "citation_required": False,
                    "risk_tier": "low",
                }
                for name in properties
            },
            document_rules=[],
        )


# 10. Retrying the same run is idempotent: identical inputs produce identical record IDs.
def test_retry_of_same_run_is_idempotent() -> None:
    fields = _nested_invoice_schema()
    ai_result = _ai_result(
        {
            "invoices": [
                {
                    "invoice_number": _scalar("INV-1"),
                    "line_items": [
                        {"description": _scalar("Widget"), "taxes": [{"amount": _scalar(1.5)}]}
                    ],
                }
            ]
        }
    )
    schema = _schema(fields)
    run = _run()
    first_records, first_fields = walk_extraction(run, schema, ai_result)
    second_records, second_fields = walk_extraction(run, schema, ai_result)

    assert [r.record_id for r in first_records] == [r.record_id for r in second_records]
    assert [r.parent_record_id for r in first_records] == [
        r.parent_record_id for r in second_records
    ]
    assert [(f.instance_path, f.value) for f in first_fields] == [
        (f.instance_path, f.value) for f in second_fields
    ]
