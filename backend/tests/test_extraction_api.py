import json
import sqlite3
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pymupdf
import pytest
from databricks.sdk.service import jobs
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.document_models import ExtractedFieldRecord, ExtractionRunRecord
from idp_app.services.document_registry import SQLiteDocumentRegistry
from idp_app.services.extraction import ExtractionService, extraction_idempotency_key
from idp_app.services.extraction_jobs import (
    DatabricksExtractionJobRunner,
    ExtractionJobPoll,
    ExtractionJobRequest,
    ExtractionJobState,
)
from idp_app.services.extraction_result import build_invoice_candidates, flatten_result
from idp_app.services.extraction_runs import SQLiteExtractionRunRepository
from idp_app.services.job_batches import batch_idempotency_token
from idp_app.services.parse_runs import SQLiteParseRunRepository
from idp_app.services.schema_registry import SQLiteSchemaRepository
from idp_app.services.schemas import load_source_manifests


def _pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Seller: Acme Supplies Ltd\n"
        "Invoice Number: INV-1042\n"
        "Invoice Date: 2026-08-29\n"
        "Subtotal: 100.00\n"
        "Discount: 5.00\n"
        "Tax: 19.00\n"
        "Total: 114.00\n"
        "Currency: GBP",
    )
    content = document.tobytes()
    document.close()
    return content


def _client(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = Settings(_env_file=None, local_data_dir=tmp_path / "idp")
    return TestClient(create_app(settings)), settings


def _upload(client: TestClient) -> dict:
    response = client.post(
        "/api/documents",
        files=[("files", ("invoice.pdf", _pdf_bytes(), "application/pdf"))],
    )
    assert response.status_code == 201
    return response.json()["documents"][0]


def _wait_parse(client: TestClient, document_id: str) -> dict:
    started = client.post(f"/api/documents/{document_id}/parse")
    assert started.status_code == 202
    for _ in range(100):
        run = client.get(f"/api/runs/{started.json()['parse_run_id']}").json()
        if run["status"] != "RUNNING":
            assert run["status"] == "SUCCESS"
            return run
        time.sleep(0.02)
    raise AssertionError("Parse did not complete")


def _wait_extraction(client: TestClient, document_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/documents/{document_id}/extraction-runs")
        assert response.status_code == 200
        run = response.json()[0]
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.02)
    raise AssertionError("Extraction did not complete")


def test_extracts_typed_invoice_with_confidence_and_resolved_citations(
    tmp_path: Path,
) -> None:
    client, settings = _client(tmp_path)
    document = _upload(client)
    parse = _wait_parse(client, document["document_id"])

    started = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    )

    assert started.status_code == 202
    assert started.json()["parse_run_id"] == parse["parse_run_id"]
    assert started.json()["options"] == {
        "version": "2.1",
        "mode": "precision",
        "enableCitations": "true",
        "enableConfidenceScores": "true",
        "idempotency_key": extraction_idempotency_key(
            document["document_id"], parse["parse_run_id"], "invoice", 1, "2.1"
        ),
    }
    completed = _wait_extraction(client, document["document_id"])
    assert completed["status"] == "EXTRACTED"

    latest_response = client.get(
        f"/api/documents/{document['document_id']}/extractions/latest"
    )
    assert latest_response.status_code == 200
    latest = latest_response.json()
    assert latest["run"]["extraction_run_id"] == completed["extraction_run_id"]
    fields = {field["field_path"]: field for field in latest["fields"]}
    assert set(fields) == {
        "invoice_number",
        "invoice_date",
        "seller_name",
        "subtotal",
        "discount",
        "tax",
        "total",
        "currency",
    }
    assert fields["invoice_number"]["value"] == "INV-1042"
    assert fields["total"]["value"] == 114.0
    assert fields["total"]["value_string"] == "114.0"
    assert fields["total"]["confidence_score"] == 0.99
    assert fields["total"]["citation_ids"] == [0]
    assert fields["total"]["citations"][0]["bbox"][0]["page_id"] == 0
    assert latest["candidates"][0]["invoice_date"] == "2026-08-29"
    assert latest["candidates"][0]["subtotal"] == "100.00"
    assert latest["candidates"][0]["discount_amount"] == "5.00"
    assert latest["candidates"][0]["tax_amount"] == "19.00"
    assert latest["candidates"][0]["total_amount"] == "114.00"
    assert latest["candidates"][0]["currency"] == "GBP"

    repository = SQLiteExtractionRunRepository(
        settings.local_data_dir / "registry.sqlite3"
    )
    retained = repository.get(completed["extraction_run_id"])
    assert retained is not None and retained.ai_result is not None
    assert retained.ai_result["metadata"]["version"] == "2.1"
    assert retained.ai_result["metadata"]["mode"] == "precision"
    assert client.get(f"/api/documents/{document['document_id']}").json()["status"] == (
        "EXTRACTED"
    )


def test_extract_requires_successful_parse(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    document = _upload(client)

    response = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SUCCESSFUL_PARSE_REQUIRED"


def test_extract_rejects_missing_or_nonproduction_schema(
    tmp_path: Path,
) -> None:
    client, settings = _client(tmp_path)
    document = _upload(client)
    _wait_parse(client, document["document_id"])
    missing = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "missing", "schema_version": 1},
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SCHEMA_NOT_FOUND"

    database = settings.local_data_dir / "registry.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_registry SET status = 'DRAFT' "
            "WHERE schema_id = 'invoice' AND schema_version = 1"
        )
    nonproduction = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    )
    assert nonproduction.status_code == 409
    assert nonproduction.json()["error"]["code"] == "SCHEMA_NOT_PRODUCTION"

    # A document is no longer tied to one use case at upload time (the generalized IDP plan
    # decouples schema selection from upload): a schema tagged for a different use case may
    # still be applied, once it is production/published again.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_registry SET status = 'PRODUCTION', use_case = 'receipt' "
            "WHERE schema_id = 'invoice' AND schema_version = 1"
        )
    decoupled = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    )
    assert decoupled.status_code == 202


def test_request_rejects_untrusted_fields_and_schema_identifier_injection(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    document = _upload(client)
    _wait_parse(client, document["document_id"])

    extra = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={
            "schema_id": "invoice",
            "schema_version": 1,
            "catalog": "browser_supplied",
        },
    )
    injected = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice'; DROP TABLE documents;--", "schema_version": 1},
    )

    assert extra.status_code == 422
    assert injected.status_code == 422


def test_generic_flattening_resolves_missing_citations_and_null_confidence(
    tmp_path: Path,
) -> None:
    repository = SQLiteSchemaRepository(tmp_path / "registry.sqlite3")
    schema = repository.register(load_source_manifests()[0], "test")
    run = ExtractionRunRecord(
        extraction_run_id="f5369a2d-aa62-47bd-b075-417b25e2b4eb",
        document_id="ce584838-9345-4223-a035-21337274dce1",
        parse_run_id="b580cfb4-e31c-49f4-a921-4d0e5ae634ab",
        schema_id="invoice",
        schema_version=1,
        schema_hash=schema.schema_hash,
        extractor_version="2.1",
        options={},
        ai_result=None,
        error_message=None,
        status="RUNNING",
        requested_by="test@example.com",
        job_run_id=None,
        started_at=datetime.now(UTC),
        completed_at=None,
    )
    raw = {
        "response": {
            path: {"value": None, "confidence_score": None, "citation_ids": []}
            for path in schema.ai_extract_schema
        },
        "metadata": {"citations": [{"id": 1, "bbox": []}]},
        "error_message": None,
    }
    raw["response"]["invoice_number"] = {
        "value": "INV-1",
        "confidence_score": "unknown",
        "citation_ids": [1, 99],
    }

    fields = flatten_result(run, schema, raw)
    invoice_number = next(field for field in fields if field.field_path == "invoice_number")
    assert len(fields) == len(schema.ai_extract_schema)
    assert invoice_number.value == "INV-1"
    assert invoice_number.confidence_score is None
    assert invoice_number.citation_ids == [1, 99]
    assert invoice_number.citations == [{"id": 1, "bbox": []}]
    assert "confidence_score is not numeric" in (invoice_number.extraction_error or "")
    assert "Missing citation metadata for IDs: 99" in (
        invoice_number.extraction_error or ""
    )


def test_successful_retry_is_immutable_and_latest_is_deterministic(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    document = _upload(client)
    _wait_parse(client, document["document_id"])

    first = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    ).json()
    assert _wait_extraction(client, document["document_id"])["status"] == "EXTRACTED"
    second = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    ).json()
    second_terminal = _wait_extraction(client, document["document_id"])

    assert second_terminal["status"] == "EXTRACTED"
    assert second["extraction_run_id"] != first["extraction_run_id"]
    history = client.get(
        f"/api/documents/{document['document_id']}/extraction-runs"
    ).json()
    assert {item["extraction_run_id"] for item in history} == {
        first["extraction_run_id"],
        second["extraction_run_id"],
    }
    latest = client.get(
        f"/api/documents/{document['document_id']}/extractions/latest"
    ).json()
    assert latest["run"]["extraction_run_id"] == second["extraction_run_id"]
    assert history[0]["options"]["idempotency_key"] == history[1]["options"][
        "idempotency_key"
    ]


class FailedPollJobRunner:
    def trigger(self, request: ExtractionJobRequest) -> int:
        del request
        return 91

    def poll(self, job_run_id: int) -> ExtractionJobPoll:
        assert job_run_id == 91
        return ExtractionJobPoll(ExtractionJobState.FAILED, "Databricks task failed")


def test_failed_run_remains_visible_and_retryable(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    document = _upload(client)
    _wait_parse(client, document["document_id"])
    database = settings.local_data_dir / "registry.sqlite3"
    documents = SQLiteDocumentRegistry(database)
    parse_runs = SQLiteParseRunRepository(database)
    schemas = SQLiteSchemaRepository(database)
    schemas.register(load_source_manifests()[0], "test")
    extraction_runs = SQLiteExtractionRunRepository(database)
    app = create_app(settings)
    app.state.extraction_service = ExtractionService(
        documents, parse_runs, schemas, extraction_runs, FailedPollJobRunner()
    )
    failing_client = TestClient(app)

    first = failing_client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    )
    history = failing_client.get(
        f"/api/documents/{document['document_id']}/extraction-runs"
    ).json()

    assert first.status_code == 202
    assert history[0]["status"] == "FAILED"
    assert history[0]["error_message"] == "Databricks task failed"
    assert documents.get(document["document_id"]).status == "EXTRACT_FAILED"  # type: ignore[union-attr]
    retry = failing_client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    )
    assert retry.status_code == 202
    assert retry.json()["extraction_run_id"] != first.json()["extraction_run_id"]


class _JobsClient:
    def __init__(self) -> None:
        self.parameters: dict[str, object] | None = None

    def run_now(self, job_id: int, **kwargs: object) -> object:
        assert job_id == 123
        self.parameters = kwargs
        return type("Wait", (), {"response": jobs.Run(run_id=456)})()


class _WorkspaceClient:
    def __init__(self) -> None:
        self.jobs = _JobsClient()


def test_databricks_submission_contains_trusted_parameters_only(tmp_path: Path) -> None:
    schema_repository = SQLiteSchemaRepository(tmp_path / "registry.sqlite3")
    schema = schema_repository.register(load_source_manifests()[0], "test")
    run = ExtractionRunRecord(
        extraction_run_id="f5369a2d-aa62-47bd-b075-417b25e2b4eb",
        document_id="ce584838-9345-4223-a035-21337274dce1",
        parse_run_id="b580cfb4-e31c-49f4-a921-4d0e5ae634ab",
        schema_id="invoice",
        schema_version=1,
        schema_hash=schema.schema_hash,
        extractor_version="2.1",
        options={},
        ai_result=None,
        error_message=None,
        status="RUNNING",
        requested_by="test@example.com",
        job_run_id=None,
        started_at=datetime.now(UTC),
        completed_at=None,
    )
    document = replace(
        _document_record(),
        document_id=run.document_id,
    )
    parse_run = replace(_parse_record(), parse_run_id=run.parse_run_id)
    workspace = _WorkspaceClient()
    runner = DatabricksExtractionJobRunner(workspace, 123)  # type: ignore[arg-type]

    job_run_id = runner.trigger([ExtractionJobRequest(run, document, parse_run, schema)])

    assert job_run_id == 456
    submitted = workspace.jobs.parameters
    # Per-document values travel as for_each inputs; nothing else is submitted.
    assert set(submitted["job_parameters"]) == {"inputs"}
    assert json.loads(submitted["job_parameters"]["inputs"]) == [
        {
            "document_id": run.document_id,
            "extraction_run_id": run.extraction_run_id,
            "schema_id": "invoice",
            "schema_version": "1",
            "requested_by": "test@example.com",
        }
    ]
    assert submitted["idempotency_token"] == batch_idempotency_token([run.extraction_run_id])


def test_a_batch_submits_every_document_as_one_job_run(tmp_path: Path) -> None:
    schema_repository = SQLiteSchemaRepository(tmp_path / "registry.sqlite3")
    schema = schema_repository.register(load_source_manifests()[0], "test")
    workspace = _WorkspaceClient()
    runner = DatabricksExtractionJobRunner(workspace, 123)  # type: ignore[arg-type]

    requests = []
    for index in range(3):
        run = replace(
            _extraction_record(schema),
            extraction_run_id=f"f5369a2d-aa62-47bd-b075-417b25e2b4e{index}",
            document_id=f"ce584838-9345-4223-a035-21337274dce{index}",
        )
        document = replace(_document_record(), document_id=run.document_id)
        parse_run = replace(_parse_record(), parse_run_id=run.parse_run_id)
        requests.append(ExtractionJobRequest(run, document, parse_run, schema))

    runner.trigger(requests)

    inputs = json.loads(workspace.jobs.parameters["job_parameters"]["inputs"])
    assert [item["document_id"] for item in inputs] == [
        request.document.document_id for request in requests
    ]
    # A batch is one job run, so its members share a single job_run_id.
    assert len(inputs) == 3


def test_an_empty_batch_is_rejected_before_reaching_databricks(tmp_path: Path) -> None:
    workspace = _WorkspaceClient()
    runner = DatabricksExtractionJobRunner(workspace, 123)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one document"):
        runner.trigger([])
    assert workspace.jobs.parameters is None


def _extraction_record(schema: object) -> ExtractionRunRecord:
    return ExtractionRunRecord(
        extraction_run_id="f5369a2d-aa62-47bd-b075-417b25e2b4eb",
        document_id="ce584838-9345-4223-a035-21337274dce1",
        parse_run_id="b580cfb4-e31c-49f4-a921-4d0e5ae634ab",
        schema_id="invoice",
        schema_version=1,
        schema_hash=schema.schema_hash,  # type: ignore[attr-defined]
        extractor_version="2.1",
        options={},
        ai_result=None,
        error_message=None,
        status="RUNNING",
        requested_by="test@example.com",
        job_run_id=None,
        started_at=datetime.now(UTC),
        completed_at=None,
    )


def _extracted_field(field_path: str, value: object) -> ExtractedFieldRecord:
    return ExtractedFieldRecord(
        extraction_run_id="f5369a2d-aa62-47bd-b075-417b25e2b4eb",
        document_id="ce584838-9345-4223-a035-21337274dce1",
        field_path=field_path,
        field_type="string",
        value=value,
        value_string=None if value is None else str(value),
        confidence_score=1.0,
        citation_ids=[],
        citations=[],
        extraction_error=None,
    )


def test_candidate_types_unambiguous_named_month_dates_and_leaves_ambiguous_null() -> None:
    run = ExtractionRunRecord(
        extraction_run_id="f5369a2d-aa62-47bd-b075-417b25e2b4eb",
        document_id="ce584838-9345-4223-a035-21337274dce1",
        parse_run_id="b580cfb4-e31c-49f4-a921-4d0e5ae634ab",
        schema_id="invoice",
        schema_version=1,
        schema_hash="hash",
        extractor_version="2.1",
        options={},
        ai_result=None,
        error_message=None,
        status="EXTRACTED",
        requested_by="test@example.com",
        job_run_id=None,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    document = _document_record()

    [named_month] = build_invoice_candidates(
        run, document, [_extracted_field("invoice_date", "28-Jul-2011")]
    )
    assert named_month.invoice_date == date(2011, 7, 28)

    [iso] = build_invoice_candidates(
        run, document, [_extracted_field("invoice_date", "2026-08-29")]
    )
    assert iso.invoice_date == date(2026, 8, 29)

    [ambiguous] = build_invoice_candidates(
        run, document, [_extracted_field("invoice_date", "07/08/2011")]
    )
    assert ambiguous.invoice_date is None


def _document_record():
    from idp_app.services.document_models import DocumentRecord

    now = datetime.now(UTC)
    return DocumentRecord(
        document_id="ce584838-9345-4223-a035-21337274dce1",
        case_id=None,
        template_id="invoice_v1",
        use_case="invoice",
        source_path="/Volumes/catalog/schema/source/incoming/document.pdf",
        file_name="invoice.pdf",
        file_size=100,
        content_sha256="a" * 64,
        selected_schema_id=None,
        selected_schema_version=None,
        status="PARSED",
        uploaded_by="test@example.com",
        uploaded_at=now,
        updated_at=now,
    )


def _parse_record():
    from idp_app.services.document_models import ParseRunRecord

    now = datetime.now(UTC)
    return ParseRunRecord(
        parse_run_id="b580cfb4-e31c-49f4-a921-4d0e5ae634ab",
        document_id="ce584838-9345-4223-a035-21337274dce1",
        content_sha256="a" * 64,
        parser_version="2.0",
        parsed={"document": {"pages": [], "elements": []}},
        document_text="Invoice",
        page_count=1,
        page_image_root="/Volumes/catalog/schema/artifacts/page_images/document/run",
        parse_error=None,
        status="SUCCESS",
        requested_by="test@example.com",
        job_run_id=1,
        started_at=now,
        completed_at=now,
    )


def _pdf_bytes_variant() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Seller: Beacon Trading Co\n"
        "Invoice Number: INV-2099\n"
        "Invoice Date: 2026-01-15\n"
        "Total: 42.00\n"
        "Currency: USD",
    )
    content = document.tobytes()
    document.close()
    return content


def test_extraction_result_endpoint_serves_historical_run_and_rejects_foreign(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    document = _upload(client)
    document_id = document["document_id"]
    _wait_parse(client, document_id)

    body = {"schema_id": "invoice", "schema_version": 1}
    assert client.post(f"/api/documents/{document_id}/extract", json=body).status_code == 202
    _wait_extraction(client, document_id)
    assert client.post(f"/api/documents/{document_id}/extract", json=body).status_code == 202
    _wait_extraction(client, document_id)

    runs = client.get(f"/api/documents/{document_id}/extraction-runs").json()
    assert len(runs) == 2
    older_run_id = runs[1]["extraction_run_id"]
    latest_run_id = runs[0]["extraction_run_id"]
    assert older_run_id != latest_run_id

    # The non-latest run remains fully inspectable with its own fields and candidate.
    historical = client.get(f"/api/documents/{document_id}/extractions/{older_run_id}")
    assert historical.status_code == 200
    payload = historical.json()
    assert payload["run"]["extraction_run_id"] == older_run_id
    assert len(payload["fields"]) == 8
    assert payload["candidates"][0]["extraction_run_id"] == older_run_id

    # An unknown run id is not found.
    unknown = client.get(
        f"/api/documents/{document_id}/extractions/00000000-0000-4000-8000-000000000000"
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "EXTRACTION_RUN_NOT_FOUND"

    # A run that belongs to a different document cannot be read through this document.
    other = client.post(
        "/api/documents",
        files=[("files", ("other.pdf", _pdf_bytes_variant(), "application/pdf"))],
    ).json()["documents"][0]
    other_id = other["document_id"]
    _wait_parse(client, other_id)
    assert client.post(f"/api/documents/{other_id}/extract", json=body).status_code == 202
    other_run = _wait_extraction(client, other_id)["extraction_run_id"]

    foreign = client.get(f"/api/documents/{document_id}/extractions/{other_run}")
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "EXTRACTION_RUN_NOT_FOUND"


def _pdf_with_lines(total: str = "500.00") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Seller: Acme Supplies Ltd\n"
        "Invoice Number: INV-3300\n"
        "Invoice Date: 2026-08-29\n"
        "LINE: 3 x Widget A @ 100.00 tax 0.00 = 300.00\n"
        "LINE: 2 x Widget B @ 100.00 tax 0.00 = 200.00\n"
        "Subtotal: 500.00\n"
        "Discount: 0.00\n"
        "Tax: 0.00\n"
        f"Total: {total}\n"
        "Currency: GBP",
    )
    content = document.tobytes()
    document.close()
    return content


def _extract_v3(client: TestClient, content: bytes, name: str) -> str:
    uploaded = client.post("/api/documents", files=[("files", (name, content, "application/pdf"))])
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documents"][0]["document_id"]
    _wait_parse(client, document_id)
    assert client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": "invoice", "schema_version": 3},
    ).status_code == 202
    assert _wait_extraction(client, document_id)["status"] == "EXTRACTED"
    return document_id


def test_typed_line_candidates_are_persisted_with_decimal_scale(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    document_id = _extract_v3(client, _pdf_with_lines(), "lines.pdf")

    repository = SQLiteExtractionRunRepository(settings.local_data_dir / "registry.sqlite3")
    run_id = client.get(f"/api/documents/{document_id}/extraction-runs").json()[0][
        "extraction_run_id"
    ]
    lines = repository.list_lines(run_id)

    assert [line.line_number for line in lines] == [1, 2]
    first, second = lines
    assert first.description == "Widget A"
    # Money keeps two places and quantity four, matching the governed column types.
    assert first.quantity == Decimal("3.0000")
    assert first.unit_price == Decimal("100.00")
    assert first.amount == Decimal("300.00")
    assert second.amount == Decimal("200.00")
    assert sum((line.amount or Decimal("0")) for line in lines) == Decimal("500.00")
    assert all(line.document_id == document_id for line in lines)


def test_an_invoice_without_lines_produces_no_line_rows(tmp_path: Path) -> None:
    """An absent line table must not become a zero-valued row that reads as a real line."""
    client, settings = _client(tmp_path)
    document_id = _extract_v3(client, _pdf_bytes(), "no-lines.pdf")

    repository = SQLiteExtractionRunRepository(settings.local_data_dir / "registry.sqlite3")
    run_id = client.get(f"/api/documents/{document_id}/extraction-runs").json()[0][
        "extraction_run_id"
    ]
    assert repository.list_lines(run_id) == []


def test_line_candidates_are_immutable_per_run(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    document_id = _extract_v3(client, _pdf_with_lines(), "first.pdf")
    assert client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": "invoice", "schema_version": 3},
    ).status_code == 202
    assert _wait_extraction(client, document_id)["status"] == "EXTRACTED"

    runs = client.get(f"/api/documents/{document_id}/extraction-runs").json()
    repository = SQLiteExtractionRunRepository(settings.local_data_dir / "registry.sqlite3")
    assert len(runs) == 2
    # Each attempt keeps its own typed lines rather than overwriting the earlier ones.
    for run in runs:
        assert len(repository.list_lines(run["extraction_run_id"])) == 2


def _etl_module() -> Any:
    """Load the Databricks projection the way the schema-hash test loads it."""
    import importlib.util
    import sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "extract_document", root / "databricks_etl/src/extract_document.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["extract_document"] = module
    spec.loader.exec_module(module)
    return module


def _leaves(*paths_and_values: tuple[str, Any]) -> list[dict[str, Any]]:
    return [{"field_path": path, "value": value} for path, value in paths_and_values]


def test_the_two_projections_agree_on_a_document_stating_several_invoices() -> None:
    """The Databricks and local projections must group repeated invoices identically.

    A nested path that the line regex failed to recognise would silently produce no typed
    lines at all, so both implementations are pinned to the same grouping here.
    """
    etl = _etl_module()
    fields = _leaves(
        ("invoices[0].invoice_number", "INV-1"),
        ("invoices[0].total", 100),
        ("invoices[0].line_items[0].amount", 60),
        ("invoices[0].line_items[1].amount", 40),
        ("invoices[1].invoice_number", "INV-2"),
        ("invoices[1].total", 250),
        ("invoices[1].line_items[0].amount", 250),
    )
    assert sorted(etl.invoice_leaves(fields)) == [0, 1]
    assert etl.invoice_leaves(fields)[1]["invoice_number"] == "INV-2"

    parameters = SimpleNamespace(
        document_id="doc-1", extraction_run_id="run-1", schema_version=4
    )
    lines = etl.build_line_candidates(parameters, fields)
    # (extraction_run_id, document_id, line_number, ..., invoice_index)
    assert [(line[2], line[-1]) for line in lines] == [(1, 0), (2, 0), (1, 1)]
    assert [line[-2] for line in lines] == [Decimal("60.00"), Decimal("40.00"), Decimal("250.00")]


def test_databricks_job_builds_the_generic_projection_during_extraction() -> None:
    etl = _etl_module()
    schema = {
        "invoices": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "string"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"amount": {"type": "number"}},
                        },
                    },
                },
            },
        }
    }
    ai_result = {
        "response": {
            "invoices": [
                {
                    "number": {"value": "INV-1"},
                    "lines": [{"amount": {"value": 25}}],
                }
            ]
        }
    }

    records, fields = etl.build_generic_projection("run-1", "doc-1", schema, ai_result)

    assert [record["instance_path"] for record in records] == [
        "$",
        "invoices[0]",
        "invoices[0].lines[0]",
    ]
    assert [field["instance_path"] for field in fields] == [
        "invoices[0].number",
        "invoices[0].lines[0].amount",
    ]
    assert all(field["record_id"] for field in fields)


def test_a_flat_contract_still_projects_one_invoice_at_index_zero() -> None:
    """Documents extracted under v1 to v3 keep projecting exactly as they always have."""
    etl = _etl_module()
    fields = _leaves(
        ("invoice_number", "INV-FLAT"),
        ("total", 75),
        ("line_items[0].amount", 50),
        ("line_items[1].amount", 25),
    )
    assert list(etl.invoice_leaves(fields)) == [0]
    parameters = SimpleNamespace(
        document_id="doc-1", extraction_run_id="run-1", schema_version=3
    )
    lines = etl.build_line_candidates(parameters, fields)
    assert [(line[2], line[-1]) for line in lines] == [(1, 0), (2, 0)]


def test_a_shape_with_no_invoice_leaves_projects_nothing() -> None:
    """A schema this projection cannot describe is captured, never written as nulls."""
    etl = _etl_module()
    parameters = SimpleNamespace(
        document_id="doc-1", extraction_run_id="run-1", schema_version=9
    )
    document = {"case_id": None, "source_path": "/x", "template_id": "t"}
    fields = _leaves(("account_number", "123"), ("transactions[0].amount", 5))
    assert etl.build_candidates(parameters, document, fields) == []
