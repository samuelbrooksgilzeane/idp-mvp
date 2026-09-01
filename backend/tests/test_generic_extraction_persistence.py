"""Unit tests for the generic record tree's write-through persistence: the recursive
walk_extraction() result is computed on the first read of a run and cached into
extracted_records / extracted_fields, so later reads (and any future direct SQL query) never
need to re-parse the raw ai_extract JSON.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from idp_app.services.document_models import (
    ExtractedFieldRecord,
    ExtractedRecordRow,
    ExtractionRunRecord,
    GenericFieldRow,
)
from idp_app.services.extraction_result import flatten_result, walk_extraction
from idp_app.services.extraction_runs import SQLiteExtractionRunRepository
from idp_app.services.generic_results import ExtractionResultsService
from idp_app.services.schema_models import ExtractField, SchemaRecord

RUN_ID = "b3f0e6b0-6a8b-4e7e-9c8e-1f6c9a5f6a1a"
DOCUMENT_ID = "8d7c6b5a-4e3f-42a1-9b8c-7d6e5f4a3b2c"


def _run(schema_hash: str = "hash") -> ExtractionRunRecord:
    now = datetime.now(UTC)
    return ExtractionRunRecord(
        extraction_run_id=RUN_ID,
        document_id=DOCUMENT_ID,
        parse_run_id="parse-1",
        schema_id="nested_invoice",
        schema_version=1,
        schema_hash=schema_hash,
        extractor_version="2.1",
        options={},
        ai_result=None,
        error_message=None,
        status="RUNNING",
        requested_by="test@example.com",
        job_run_id=None,
        started_at=now,
        completed_at=None,
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
            "line_items": ExtractField(type="array", description="Billed lines.", items=line_item),
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


def _flat_ai_result() -> dict:
    return {
        "response": {"total": _scalar(114.0)},
        "error_message": None,
        "metadata": {"citations": []},
    }


def _nested_ai_result() -> dict:
    return {
        "response": {
            "invoices": [
                {
                    "invoice_number": _scalar("INV-1"),
                    "line_items": [
                        {
                            "description": _scalar("Widget"),
                            "taxes": [{"amount": _scalar(1.5)}],
                        }
                    ],
                }
            ]
        },
        "error_message": None,
        "metadata": {"citations": []},
    }


def test_field_path_and_instance_path_use_the_same_indexing_convention() -> None:
    """Guard test: the write-through UPDATE matches an existing extracted_fields row on
    `field_path = instance_path`. If flatten_result's field_path ever stops using the same
    literal-index convention as walk_extraction's instance_path, that match silently stops
    working (falling back to a defensive insert) rather than failing loudly -- so this
    assumption gets its own explicit test.
    """
    run = _run()
    schema = _nested_schema()
    ai_result = _nested_ai_result()

    flat_fields = flatten_result(run, schema, ai_result)
    _records, generic_fields = walk_extraction(run, schema, ai_result)

    flat_paths = {field.field_path for field in flat_fields}
    instance_paths = {field.instance_path for field in generic_fields}
    assert flat_paths == instance_paths
    assert flat_paths == {
        "invoices[0].invoice_number",
        "invoices[0].line_items[0].description",
        "invoices[0].line_items[0].taxes[0].amount",
    }


def test_persist_generic_enriches_the_existing_row_without_duplicating_it(
    tmp_path: Path,
) -> None:
    repository = SQLiteExtractionRunRepository(tmp_path / "registry.sqlite3")
    run = _run()
    repository.create(run)
    repository.retain_raw(run.extraction_run_id, _flat_ai_result())

    # Seed the row the way the extraction job actually does at completion time.
    seeded_field = ExtractedFieldRecord(
        extraction_run_id=run.extraction_run_id,
        document_id=run.document_id,
        field_path="total",
        field_type="number",
        value=114.0,
        value_string="114.0",
        confidence_score=0.9,
        citation_ids=[],
        citations=[],
        extraction_error=None,
    )
    repository.complete(run.extraction_run_id, [seeded_field], [], [])

    record = ExtractedRecordRow(
        run_id=run.extraction_run_id,
        document_id=run.document_id,
        record_id="root-record",
        parent_record_id=None,
        schema_path="$",
        instance_path="$",
        ordinal=None,
    )
    field = GenericFieldRow(
        run_id=run.extraction_run_id,
        document_id=run.document_id,
        record_id="root-record",
        schema_path="total",
        instance_path="total",
        field_name="total",
        declared_type="number",
        value=114.0,
        value_string="114.0",
        confidence_score=0.9,
        citation_ids=[],
        citations=[],
        validation_status=None,
        validation_message=None,
    )

    repository.persist_generic([record], [field])

    persisted_records = repository.list_generic_records(run.extraction_run_id)
    assert persisted_records == [record]
    persisted_fields = repository.list_generic_fields(run.extraction_run_id)
    assert len(persisted_fields) == 1
    assert persisted_fields[0].field_name == "total"
    assert persisted_fields[0].value == 114.0

    # The enrichment must not have produced a second extracted_fields row for this leaf.
    all_fields = repository.list_fields(run.extraction_run_id)
    assert len(all_fields) == 1
    assert all_fields[0].field_path == "total"

    # Idempotent: persisting the same input again changes nothing.
    repository.persist_generic([record], [field])
    assert repository.list_generic_records(run.extraction_run_id) == [record]
    assert len(repository.list_fields(run.extraction_run_id)) == 1


def test_persist_generic_inserts_a_field_row_when_no_flatten_result_row_exists(
    tmp_path: Path,
) -> None:
    """Defensive fallback: if walk_extraction ever finds a leaf flatten_result did not
    (should not normally happen, since both walk the same schema), the field must still be
    captured rather than silently dropped."""
    repository = SQLiteExtractionRunRepository(tmp_path / "registry.sqlite3")
    run = _run()
    repository.create(run)
    repository.retain_raw(run.extraction_run_id, _flat_ai_result())
    repository.complete(run.extraction_run_id, [], [], [])

    record = ExtractedRecordRow(
        run_id=run.extraction_run_id,
        document_id=run.document_id,
        record_id="root-record",
        parent_record_id=None,
        schema_path="$",
        instance_path="$",
        ordinal=None,
    )
    field = GenericFieldRow(
        run_id=run.extraction_run_id,
        document_id=run.document_id,
        record_id="root-record",
        schema_path="total",
        instance_path="total",
        field_name="total",
        declared_type="number",
        value=114.0,
        value_string="114.0",
        confidence_score=0.9,
        citation_ids=[],
        citations=[],
        validation_status=None,
        validation_message=None,
    )

    repository.persist_generic([record], [field])

    persisted_fields = repository.list_generic_fields(run.extraction_run_id)
    assert len(persisted_fields) == 1
    assert persisted_fields[0].value == 114.0


class _RecordsOnlyRepository:
    """A repository stand-in for an environment where extracted_records is writable but
    extracted_fields is not (the deployed app currently has no MODIFY grant on
    extracted_fields, only on extracted_records) -- records persist, but a field-enriching
    write always fails silently and list_generic_fields stays permanently empty.
    """

    def __init__(self, run: ExtractionRunRecord) -> None:
        self._run = run
        self._records: list[ExtractedRecordRow] = []
        self.persist_calls = 0

    def get(self, extraction_run_id: str) -> ExtractionRunRecord | None:
        return self._run if extraction_run_id == self._run.extraction_run_id else None

    def list_generic_records(self, extraction_run_id: str) -> list[ExtractedRecordRow]:
        return list(self._records)

    def list_generic_fields(self, extraction_run_id: str) -> list[GenericFieldRow]:
        return []  # extracted_fields is never actually enriched under this grant.

    def persist_generic(
        self, records: list[ExtractedRecordRow], fields: list[GenericFieldRow]
    ) -> None:
        self.persist_calls += 1
        self._records = list(records)  # the records write succeeds...
        # ...the fields write does not.
        raise PermissionError("no MODIFY grant on extracted_fields")


def test_records_only_cache_still_recomputes_fields_rather_than_dropping_them() -> None:
    """Guards the exact partial-grant deployment this app currently runs under: if only
    extracted_records is writable, a later read must not return the cached records paired
    with an empty fields list (which would silently drop every field's confidence/citation/
    validation data) -- it must recompute the full result instead.
    """
    schema = _nested_schema()
    ai_result = _nested_ai_result()
    run = replace(_run(), ai_result=ai_result)
    expected_records, expected_fields = walk_extraction(run, schema, ai_result)
    assert expected_records and expected_fields  # the fixture must actually produce both.

    repository = _RecordsOnlyRepository(run)
    service = ExtractionResultsService(repository, schemas=None, documents=None)  # type: ignore[arg-type]

    async def _twice() -> tuple[object, object]:
        first = await service._records_and_fields(run, schema)  # noqa: SLF001
        second = await service._records_and_fields(run, schema)  # noqa: SLF001
        return first, second

    (first_records, first_fields), (second_records, second_fields) = asyncio.run(_twice())

    # Review reads never attempt a write, even when only half of a legacy projection exists.
    assert repository.persist_calls == 0
    assert len(first_records) == len(expected_records)
    assert len(first_fields) == len(expected_fields)
    assert len(second_records) == len(expected_records)
    assert len(second_fields) == len(expected_fields)
