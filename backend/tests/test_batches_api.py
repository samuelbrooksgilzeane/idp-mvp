"""Batch submission: one job run per batch, with per-document isolation."""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app


def _pdf(invoice_number: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Seller: Acme Supplies Ltd\n"
        f"Invoice Number: {invoice_number}\n"
        "Invoice Date: 2026-08-29\n"
        "LINE: 3 x Widget A @ 100.00 tax 0.00 = 300.00\n"
        "Subtotal: 300.00\nDiscount: 0.00\nTax: 0.00\nTotal: 300.00\nCurrency: GBP",
    )
    content = document.tobytes()
    document.close()
    return content


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path / "idp")))


def _upload(client: TestClient, count: int) -> list[str]:
    files = [
        ("files", (f"invoice-{index}.pdf", _pdf(f"INV-{index}"), "application/pdf"))
        for index in range(count)
    ]
    response = client.post("/api/documents", files=files)
    assert response.status_code == 201
    return [document["document_id"] for document in response.json()["documents"]]


def _await_batch(client: TestClient, kind: str, job_run_id: int) -> dict:
    for _ in range(150):
        status = client.get(f"/api/batches/{kind}/{job_run_id}").json()
        if status["running"] == 0:
            return status
        time.sleep(0.02)
    raise AssertionError(f"{kind} batch did not settle")


def test_a_parse_batch_runs_every_document_under_one_job_run(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_ids = _upload(client, 3)

    response = client.post("/api/batches/parse", json={"document_ids": document_ids})

    assert response.status_code == 202
    batch = response.json()
    assert batch["kind"] == "parse"
    assert batch["requested"] == 3 and batch["accepted"] == 3
    assert batch["errors"] == []
    # Every member shares the one batch identity.
    assert batch["job_run_id"] is not None
    assert {member["document_id"] for member in batch["members"]} == set(document_ids)

    status = _await_batch(client, "parse", batch["job_run_id"])
    assert status["total"] == 3 and status["succeeded"] == 3 and status["failed"] == 0
    for document_id in document_ids:
        assert client.get(f"/api/documents/{document_id}").json()["status"] == "PARSED"


def test_an_extract_batch_follows_a_parse_batch(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_ids = _upload(client, 2)
    parse_batch = client.post("/api/batches/parse", json={"document_ids": document_ids}).json()
    _await_batch(client, "parse", parse_batch["job_run_id"])

    response = client.post(
        "/api/batches/extract",
        json={"document_ids": document_ids, "schema_id": "invoice", "schema_version": 3},
    )

    assert response.status_code == 202
    batch = response.json()
    assert batch["kind"] == "extract" and batch["accepted"] == 2
    status = _await_batch(client, "extract", batch["job_run_id"])
    assert status["succeeded"] == 2
    for document_id in document_ids:
        latest = client.get(f"/api/documents/{document_id}/extractions/latest").json()
        assert latest["run"]["job_run_id"] == batch["job_run_id"]


def test_an_ineligible_document_is_reported_without_stopping_the_batch(tmp_path: Path) -> None:
    """A document failing its own preconditions must not deny the rest of the batch."""
    client = _client(tmp_path)
    document_ids = _upload(client, 2)
    missing = "00000000-0000-4000-8000-000000000000"

    batch = client.post(
        "/api/batches/parse", json={"document_ids": [*document_ids, missing]}
    ).json()

    assert batch["requested"] == 3
    assert batch["accepted"] == 2
    assert [error["document_id"] for error in batch["errors"]] == [missing]
    assert batch["errors"][0]["code"] == "DOCUMENT_NOT_FOUND"
    status = _await_batch(client, "parse", batch["job_run_id"])
    assert status["succeeded"] == 2


def test_extract_batch_reports_documents_that_were_never_parsed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_ids = _upload(client, 2)

    batch = client.post(
        "/api/batches/extract",
        json={"document_ids": document_ids, "schema_id": "invoice", "schema_version": 3},
    ).json()

    assert batch["accepted"] == 0
    assert batch["job_run_id"] is None
    assert {error["code"] for error in batch["errors"]} == {"SUCCESSFUL_PARSE_REQUIRED"}


def test_a_repeated_document_is_submitted_once(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_ids = _upload(client, 1)

    batch = client.post(
        "/api/batches/parse", json={"document_ids": document_ids * 3}
    ).json()

    assert batch["requested"] == 1 and batch["accepted"] == 1
    assert len(batch["members"]) == 1


def test_batch_requests_reject_untrusted_and_empty_input(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/batches/parse", json={"document_ids": []}).status_code == 422
    assert client.post(
        "/api/batches/parse",
        json={"document_ids": ["a"], "table_name": "workspace.idp_mvp.idp_dev_documents"},
    ).status_code == 422
    assert client.post(
        "/api/batches/extract",
        json={"document_ids": ["a"], "schema_id": "Invoice; DROP", "schema_version": 1},
    ).status_code == 422


def test_an_unknown_batch_is_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/batches/parse/987654").status_code == 404
    assert client.get("/api/batches/extract/987654").status_code == 404
