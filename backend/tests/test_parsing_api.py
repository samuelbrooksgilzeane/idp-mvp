import time
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.document_models import DocumentRecord
from idp_app.services.document_registry import SQLiteDocumentRegistry
from idp_app.services.parse_jobs import (
    ParseJobPoll,
    ParseJobRequest,
    ParseJobState,
)
from idp_app.services.parse_runs import SQLiteParseRunRepository
from idp_app.services.parsing import ParsingService


def _pdf_bytes(text: str = "Invoice 1042\nTotal GBP 120.00") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def _client(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = Settings(_env_file=None, local_data_dir=tmp_path / "idp")
    return TestClient(create_app(settings)), settings


def _upload(client: TestClient, content: bytes, name: str = "invoice.pdf") -> dict:
    response = client.post(
        "/api/documents",
        files=[("files", (name, content, "application/pdf"))],
    )
    assert response.status_code == 201
    return response.json()["documents"][0]


def _wait_for_terminal(client: TestClient, parse_run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/runs/{parse_run_id}")
        assert response.status_code == 200
        run = response.json()
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.02)
    raise AssertionError("Parse run did not reach a terminal state")


def test_success_retains_raw_result_and_page_images(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    uploaded = _upload(client, _pdf_bytes())

    started = client.post(f"/api/documents/{uploaded['document_id']}/parse")

    assert started.status_code == 202
    assert started.json()["status"] == "RUNNING"
    completed = _wait_for_terminal(client, started.json()["parse_run_id"])
    assert completed["status"] == "SUCCESS"
    assert completed["parser_version"] == "2.0"
    assert completed["page_count"] == 1

    document = client.get(f"/api/documents/{uploaded['document_id']}").json()
    assert document["status"] == "PARSED"
    repository = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    retained = repository.get(completed["parse_run_id"])
    assert retained is not None
    assert retained.parsed is not None
    assert retained.parsed["metadata"]["version"] == "2.0"
    assert retained.document_text is not None
    assert "Invoice 1042" in retained.document_text
    expected_root = settings.local_data_dir / "artifacts_volume" / "page_images"
    assert Path(retained.page_image_root).is_relative_to(expected_root)
    page_uri = Path(retained.parsed["document"]["pages"][0]["image_uri"])
    assert page_uri.is_relative_to(expected_root)
    assert page_uri.is_file()


def test_parse_failure_is_visible_retryable_and_preserves_source(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    uploaded = _upload(client, b"%PDF-1.7\nnot a valid pdf", "broken.pdf")

    first = client.post(f"/api/documents/{uploaded['document_id']}/parse").json()
    first_terminal = _wait_for_terminal(client, first["parse_run_id"])
    assert first_terminal["status"] == "FAILED"
    assert first_terminal["parse_error"]
    assert client.get(f"/api/documents/{uploaded['document_id']}").json()["status"] == (
        "PARSE_FAILED"
    )

    second_response = client.post(f"/api/documents/{uploaded['document_id']}/parse")
    assert second_response.status_code == 202
    second = second_response.json()
    assert second["parse_run_id"] != first["parse_run_id"]
    assert _wait_for_terminal(client, second["parse_run_id"])["status"] == "FAILED"

    history = client.get(
        f"/api/documents/{uploaded['document_id']}/parse-runs"
    ).json()
    assert {run["parse_run_id"] for run in history} == {
        first["parse_run_id"],
        second["parse_run_id"],
    }
    registry = SQLiteDocumentRegistry(settings.local_data_dir / "registry.sqlite3")
    source = registry.get(uploaded["document_id"])
    assert source is not None
    assert Path(source.source_path).is_file()


def test_retry_after_success_keeps_history_and_latest_success(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    uploaded = _upload(client, _pdf_bytes("Invoice retry test"))

    first = client.post(f"/api/documents/{uploaded['document_id']}/parse").json()
    assert _wait_for_terminal(client, first["parse_run_id"])["status"] == "SUCCESS"
    second = client.post(f"/api/documents/{uploaded['document_id']}/parse").json()
    assert _wait_for_terminal(client, second["parse_run_id"])["status"] == "SUCCESS"

    repository = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    history = repository.list_for_document(uploaded["document_id"])
    assert len(history) == 2
    assert {run.parse_run_id for run in history} == {
        first["parse_run_id"],
        second["parse_run_id"],
    }
    latest = repository.latest_successful(uploaded["document_id"])
    assert latest is not None
    assert latest.parse_run_id == second["parse_run_id"]


def test_document_cannot_start_two_parse_runs_concurrently(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    uploaded = _upload(client, _pdf_bytes("Concurrent parse guard"))

    first = client.post(f"/api/documents/{uploaded['document_id']}/parse")
    second = client.post(f"/api/documents/{uploaded['document_id']}/parse")

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DOCUMENT_NOT_PARSEABLE"
    _wait_for_terminal(client, first.json()["parse_run_id"])


class FailingJobRunner:
    def trigger(self, request: ParseJobRequest) -> int:
        del request
        raise RuntimeError("job service unavailable")

    def poll(self, job_run_id: int) -> ParseJobPoll:
        del job_run_id
        return ParseJobPoll(ParseJobState.FAILED)


class FailedPollJobRunner:
    def trigger(self, request: ParseJobRequest) -> int:
        del request
        return 91

    def poll(self, job_run_id: int) -> ParseJobPoll:
        assert job_run_id == 91
        return ParseJobPoll(ParseJobState.FAILED, "Databricks task failed")


def _registered_document(registry: SQLiteDocumentRegistry, source: Path) -> DocumentRecord:
    now = datetime.now(UTC)
    document = DocumentRecord(
        document_id="02bb6168-b472-47c4-8f6c-9103872f10a7",
        case_id=None,
        template_id="invoice_v1",
        use_case="invoice",
        source_path=source.as_posix(),
        file_name="invoice.pdf",
        file_size=source.stat().st_size,
        content_sha256="b" * 64,
        selected_schema_id=None,
        selected_schema_version=None,
        status="UPLOADED",
        uploaded_by="test@example.com",
        uploaded_at=now,
        updated_at=now,
    )
    registry.add(document)
    return document


def test_trigger_failure_marks_run_and_document_failed(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, local_data_dir=tmp_path / "idp")
    source = tmp_path / "invoice.pdf"
    source.write_bytes(_pdf_bytes())
    registry = SQLiteDocumentRegistry(settings.local_data_dir / "registry.sqlite3")
    document = _registered_document(registry, source)
    runs = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    app = create_app(settings)
    app.state.parsing_service = ParsingService(
        settings, registry, runs, FailingJobRunner()
    )
    client = TestClient(app)

    response = client.post(f"/api/documents/{document.document_id}/parse")

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PARSE_JOB_TRIGGER_FAILED"
    assert registry.get(document.document_id).status == "PARSE_FAILED"  # type: ignore[union-attr]
    history = runs.list_for_document(document.document_id)
    assert len(history) == 1
    assert history[0].status == "FAILED"


def test_polling_failure_marks_run_and_document_failed(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, local_data_dir=tmp_path / "idp")
    source = tmp_path / "invoice.pdf"
    source.write_bytes(_pdf_bytes())
    registry = SQLiteDocumentRegistry(settings.local_data_dir / "registry.sqlite3")
    document = _registered_document(registry, source)
    runs = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    app = create_app(settings)
    app.state.parsing_service = ParsingService(
        settings, registry, runs, FailedPollJobRunner()
    )
    client = TestClient(app)

    started = client.post(f"/api/documents/{document.document_id}/parse")
    polled = client.get(f"/api/runs/{started.json()['parse_run_id']}")

    assert started.status_code == 202
    assert polled.json()["status"] == "FAILED"
    assert polled.json()["parse_error"] == {
        "error_message": "Databricks task failed"
    }
    assert registry.get(document.document_id).status == "PARSE_FAILED"  # type: ignore[union-attr]
