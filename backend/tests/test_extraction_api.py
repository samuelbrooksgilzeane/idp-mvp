import sqlite3
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pymupdf
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
from idp_app.services.extraction_result import build_invoice_candidate, flatten_result
from idp_app.services.extraction_runs import SQLiteExtractionRunRepository
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
    assert latest["candidate"]["invoice_date"] == "2026-08-29"
    assert latest["candidate"]["subtotal"] == "100.00"
    assert latest["candidate"]["discount_amount"] == "5.00"
    assert latest["candidate"]["tax_amount"] == "19.00"
    assert latest["candidate"]["total_amount"] == "114.00"
    assert latest["candidate"]["currency"] == "GBP"

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


def test_extract_rejects_missing_nonproduction_and_use_case_mismatch(
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

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_registry SET status = 'PRODUCTION', use_case = 'receipt' "
            "WHERE schema_id = 'invoice' AND schema_version = 1"
        )
    mismatch = client.post(
        f"/api/documents/{document['document_id']}/extract",
        json={"schema_id": "invoice", "schema_version": 1},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "SCHEMA_USE_CASE_MISMATCH"


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

    job_run_id = runner.trigger(ExtractionJobRequest(run, document, parse_run, schema))

    assert job_run_id == 456
    assert workspace.jobs.parameters == {
        "idempotency_token": run.extraction_run_id,
        "job_parameters": {
            "document_id": run.document_id,
            "extraction_run_id": run.extraction_run_id,
            "schema_id": "invoice",
            "schema_version": "1",
            "requested_by": "test@example.com",
        },
    }


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

    named_month = build_invoice_candidate(
        run, document, [_extracted_field("invoice_date", "28-Jul-2011")]
    )
    assert named_month.invoice_date == date(2011, 7, 28)

    iso = build_invoice_candidate(
        run, document, [_extracted_field("invoice_date", "2026-08-29")]
    )
    assert iso.invoice_date == date(2026, 8, 29)

    ambiguous = build_invoice_candidate(
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
