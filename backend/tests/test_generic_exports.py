"""Unit tests for the schema-driven export builder (section 7): relational tables built
directly from the generic walker's output, independent of the mock extraction job runner.
"""

from __future__ import annotations

from datetime import UTC, datetime

from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.exports import build_export_tables
from idp_app.services.extraction_result import walk_extraction
from idp_app.services.schema_models import ExtractField, SchemaRecord

RUN_ID = "f5369a2d-aa62-47bd-b075-417b25e2b4eb"
DOCUMENT_ID = "ce584838-9345-4223-a035-21337274dce1"


def _run() -> ExtractionRunRecord:
    now = datetime.now(UTC)
    return ExtractionRunRecord(
        extraction_run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        parse_run_id="b580cfb4-e31c-49f4-a921-4d0e5ae634ab",
        schema_id="nested_invoice",
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


def _scalar(value: object) -> dict:
    return {"value": value, "confidence_score": 0.9, "citation_ids": []}


def _nested_schema() -> SchemaRecord:
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
            "seller": ExtractField(
                type="object",
                description="Seller details.",
                properties={"name": ExtractField(type="string", description="Seller name.")},
            ),
            "line_items": ExtractField(
                type="array", description="Billed lines.", items=line_item
            ),
        },
    )
    fields = {"invoices": ExtractField(type="array", description="Invoices.", items=invoice)}
    return SchemaRecord(
        schema_id="nested_invoice",
        schema_version=1,
        display_name="Nested invoice",
        use_case="generic",
        ai_extract_schema=fields,
        instructions="Test schema.",
        field_policies={},
        document_rules=[],
        schema_hash="hash",
        status="PUBLISHED",
        created_by="tester",
        created_at=datetime.now(UTC),
    )


def test_nested_invoice_exports_into_related_tables_without_cartesian_duplication() -> None:
    schema = _nested_schema()
    ai_result = {
        "response": {
            "invoices": [
                {
                    "invoice_number": _scalar("INV-1"),
                    "seller": {"name": _scalar("Acme")},
                    "line_items": [
                        {
                            "description": _scalar("Widget"),
                            "taxes": [{"amount": _scalar(1.5)}, {"amount": _scalar(0.5)}],
                        },
                        {"description": _scalar("Gadget"), "taxes": []},
                    ],
                },
                {
                    "invoice_number": _scalar("INV-2"),
                    "seller": {"name": _scalar("Beta")},
                    "line_items": [],
                },
            ]
        },
        "error_message": None,
        "metadata": {"citations": []},
    }
    run = _run()
    records, fields = walk_extraction(run, schema, ai_result)
    tables = build_export_tables(run, records, fields, "three_invoices.pdf")
    by_name = {table.name: table for table in tables}

    # One table per repeated collection: the root, invoices, line items and their nested taxes.
    assert set(by_name) == {"Document", "Invoices", "Line_Items", "Taxes"}

    invoices = by_name["Invoices"]
    assert len(invoices.rows) == 2
    # A singleton nested object (seller) is flattened onto its owning table with a dotted
    # column name, not exported as its own sheet.
    assert "seller.name" in invoices.columns
    names = {row["seller.name"] for row in invoices.rows}
    assert names == {"Acme", "Beta"}
    numbers = {row["invoice_number"] for row in invoices.rows}
    assert numbers == {"INV-1", "INV-2"}

    line_items = by_name["Line_Items"]
    # Two lines under the first invoice, none under the second: no Cartesian duplication.
    assert len(line_items.rows) == 2
    invoice_record_ids = {row["_record_id"] for row in invoices.rows}
    assert {row["_parent_record_id"] for row in line_items.rows} <= invoice_record_ids
    # Both lines belong to the same invoice.
    assert len({row["_parent_record_id"] for row in line_items.rows}) == 1

    taxes = by_name["Taxes"]
    assert len(taxes.rows) == 2
    line_item_record_ids = {row["_record_id"] for row in line_items.rows}
    assert all(row["_parent_record_id"] in line_item_record_ids for row in taxes.rows)
    assert {row["amount"] for row in taxes.rows} == {1.5, 0.5}

    # Every table row carries the relationship columns first.
    for table in tables:
        assert table.columns[:4] == ["_document_id", "_record_id", "_parent_record_id", "_ordinal"]
        assert all(row["_document_id"] == DOCUMENT_ID for row in table.rows)
