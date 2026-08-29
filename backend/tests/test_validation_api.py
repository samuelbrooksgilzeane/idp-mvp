"""End-to-end deterministic validation through the API in mock mode."""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app


def _pdf(total: str = "114.00") -> bytes:
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
        f"Total: {total}\n"
        "Currency: GBP",
    )
    content = document.tobytes()
    document.close()
    return content


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path / "idp")))


def _prepare(client: TestClient, content: bytes, name: str = "invoice.pdf") -> str:
    uploaded = client.post("/api/documents", files=[("files", (name, content, "application/pdf"))])
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documents"][0]["document_id"]

    started = client.post(f"/api/documents/{document_id}/parse")
    assert started.status_code == 202
    for _ in range(100):
        run = client.get(f"/api/runs/{started.json()['parse_run_id']}").json()
        if run["status"] != "RUNNING":
            assert run["status"] == "SUCCESS"
            break
        time.sleep(0.02)

    assert client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": "invoice", "schema_version": 2},
    ).status_code == 202
    for _ in range(100):
        runs = client.get(f"/api/documents/{document_id}/extraction-runs").json()
        if runs and runs[0]["status"] != "RUNNING":
            assert runs[0]["status"] == "EXTRACTED"
            break
        time.sleep(0.02)
    return document_id


def test_balanced_invoice_validates_and_reports_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _prepare(client, _pdf())

    response = client.post(f"/api/documents/{document_id}/validate", json={})
    assert response.status_code == 201
    report = response.json()

    assert report["run"]["document_status"] == "VALIDATED_PASS"
    assert report["run"]["schema_version"] == 2
    assert report["run"]["validator_version"]
    assert report["summary"]["total"] == len(report["results"])
    assert report["summary"]["blocking"] == 0

    by_rule = {result["rule_id"] for result in report["results"]}
    assert {"provenance", "grounding", "cast_integrity", "citation_presence"} <= by_rule
    reconciliation = [
        r for r in report["results"] if r["rule_id"] == "invoice_total_reconciliation"
    ]
    assert [r["status"] for r in reconciliation] == ["PASS"]

    assert client.get(f"/api/documents/{document_id}").json()["status"] == "VALIDATED_PASS"


def test_unbalanced_invoice_requires_review_and_is_traceable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _prepare(client, _pdf(total="999.00"))

    report = client.post(f"/api/documents/{document_id}/validate", json={}).json()
    assert report["run"]["document_status"] == "REVIEW_REQUIRED"

    failure = next(
        r for r in report["results"] if r["rule_id"] == "invoice_total_reconciliation"
    )
    assert failure["status"] == "FAIL"
    assert failure["severity"] == "BLOCKING"
    assert failure["actual_value"] == "999.0"
    assert failure["expected_value"] == "114.0"
    assert failure["field_path"] == "total"

    assert client.get(f"/api/documents/{document_id}").json()["status"] == "REVIEW_REQUIRED"


def test_validation_history_summary_and_historical_run_are_available(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _prepare(client, _pdf())

    first = client.post(f"/api/documents/{document_id}/validate", json={}).json()
    second = client.post(f"/api/documents/{document_id}/validate", json={}).json()
    assert first["run"]["validation_run_id"] != second["run"]["validation_run_id"]

    runs = client.get(f"/api/documents/{document_id}/validation-runs").json()
    assert [run["validation_run_id"] for run in runs][0] == second["run"]["validation_run_id"]
    assert len(runs) == 2

    latest = client.get(f"/api/documents/{document_id}/validations/latest").json()
    assert latest["run"]["validation_run_id"] == second["run"]["validation_run_id"]

    summary = client.get(f"/api/documents/{document_id}/validation-summary").json()
    assert summary["total"] == latest["summary"]["total"]

    historical = client.get(
        f"/api/documents/{document_id}/validations/{first['run']['validation_run_id']}"
    ).json()
    assert historical["run"]["validation_run_id"] == first["run"]["validation_run_id"]


def test_validation_requires_a_successful_extraction(tmp_path: Path) -> None:
    client = _client(tmp_path)
    uploaded = client.post(
        "/api/documents", files=[("files", ("a.pdf", _pdf(), "application/pdf"))]
    )
    document_id = uploaded.json()["documents"][0]["document_id"]

    response = client.post(f"/api/documents/{document_id}/validate", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SUCCESSFUL_EXTRACTION_REQUIRED"


def test_unknown_document_and_run_are_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    missing = "00000000-0000-4000-8000-000000000000"
    assert client.post(f"/api/documents/{missing}/validate", json={}).status_code == 404
    assert client.get(f"/api/documents/{missing}/validations/latest").status_code == 404

    document_id = _prepare(client, _pdf())
    unknown = client.get(f"/api/documents/{document_id}/validations/{missing}")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "VALIDATION_RUN_NOT_FOUND"


def test_validation_rejects_untrusted_request_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _prepare(client, _pdf())
    response = client.post(
        f"/api/documents/{document_id}/validate",
        json={"table_name": "workspace.idp_mvp.idp_dev_documents"},
    )
    assert response.status_code == 422


def test_business_duplicate_invoice_is_detected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = _prepare(client, _pdf(), name="first.pdf")
    # A different file that reports the same seller and invoice number.
    second = _prepare(client, _pdf(total="114.00 "), name="second.pdf")

    report = client.post(f"/api/documents/{second}/validate", json={}).json()
    duplicate = next(r for r in report["results"] if r["rule_id"] == "duplicate_document")
    assert duplicate["status"] == "FAIL"
    assert first in (duplicate["evidence"] or "")


def _pdf_with_lines(total: str = "340.97") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Seller: Acme Supplies Ltd\n"
        "Invoice Number: INV-2050\n"
        "Invoice Date: 2026-08-29\n"
        "LINE: 3 x Widget A @ 91.65 tax 0.00 = 274.95\n"
        "LINE: 2 x Widget B @ 33.01 tax 0.00 = 66.02\n"
        "Subtotal: 340.97\n"
        "Discount: 0.00\n"
        "Tax: 0.00\n"
        f"Total: {total}\n"
        "Currency: GBP",
    )
    content = document.tobytes()
    document.close()
    return content


def _prepare_v3(client: TestClient, content: bytes, name: str) -> str:
    uploaded = client.post("/api/documents", files=[("files", (name, content, "application/pdf"))])
    assert uploaded.status_code == 201
    document_id = uploaded.json()["documents"][0]["document_id"]
    started = client.post(f"/api/documents/{document_id}/parse")
    for _ in range(100):
        if client.get(f"/api/runs/{started.json()['parse_run_id']}").json()["status"] != "RUNNING":
            break
        time.sleep(0.02)
    assert client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": "invoice", "schema_version": 3},
    ).status_code == 202
    for _ in range(100):
        runs = client.get(f"/api/documents/{document_id}/extraction-runs").json()
        if runs and runs[0]["status"] != "RUNNING":
            assert runs[0]["status"] == "EXTRACTED"
            break
        time.sleep(0.02)
    return document_id


def test_line_items_are_extracted_and_reconcile_end_to_end(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _prepare_v3(client, _pdf_with_lines(), "lines.pdf")

    latest = client.get(f"/api/documents/{document_id}/extractions/latest").json()
    paths = {field["field_path"] for field in latest["fields"]}
    assert "line_items[0].amount" in paths and "line_items[1].amount" in paths
    assert "line_items[2].amount" not in paths
    lines = {f["field_path"]: f for f in latest["fields"]}
    assert lines["line_items[0].description"]["value"] == "Widget A"
    assert lines["line_items[0].amount"]["value"] == 274.95
    assert lines["line_items[0].amount"]["confidence_score"] == 0.99
    assert lines["line_items[0].amount"]["citations"][0]["bbox"][0]["page_id"] == 0

    report = client.post(f"/api/documents/{document_id}/validate", json={}).json()
    reconciliation = next(
        r for r in report["results"] if r["rule_id"] == "line_items_reconcile_to_total"
    )
    assert reconciliation["status"] == "PASS"
    assert report["run"]["document_status"] == "VALIDATED_PASS"


def test_line_items_that_do_not_add_up_are_blocked(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _prepare_v3(client, _pdf_with_lines(total="999.00"), "bad-lines.pdf")

    report = client.post(f"/api/documents/{document_id}/validate", json={}).json()
    reconciliation = next(
        r for r in report["results"] if r["rule_id"] == "line_items_reconcile_to_total"
    )
    assert reconciliation["status"] == "FAIL"
    assert reconciliation["severity"] == "BLOCKING"
    assert reconciliation["expected_value"] == "340.97"
    assert report["run"]["document_status"] == "REVIEW_REQUIRED"
