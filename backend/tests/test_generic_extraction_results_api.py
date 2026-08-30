"""End-to-end coverage for the generalized IDP flow (sections 2, 3, 6 and 7):
create a custom schema, publish it, extract a document with it, and read back the
schema-agnostic results and export -- with no invoice-specific field names anywhere.
"""

from __future__ import annotations

import io
import sqlite3
import time
import zipfile
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app


def _pdf_bytes() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "Seller: Acme Supplies Ltd\nTotal: 114.00\n",
    )
    content = document.tobytes()
    document.close()
    return content


def _client(tmp_path: Path) -> TestClient:
    settings = Settings(_env_file=None, local_data_dir=tmp_path / "idp")
    return TestClient(create_app(settings))


def _upload_and_parse(client: TestClient) -> str:
    uploaded = client.post(
        "/api/documents",
        files=[("files", ("doc.pdf", _pdf_bytes(), "application/pdf"))],
    )
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
    return document_id


def _create_and_publish_flat_schema(client: TestClient) -> dict:
    created = client.post(
        "/api/schemas",
        json={
            "display_name": "Custom Vendor Summary",
            "root_mode": "SINGLE_RECORD",
            "description": "A minimal custom (non-invoice) schema.",
        },
    ).json()
    schema_id = created["schema_id"]
    updated = client.put(
        f"/api/schemas/{schema_id}/draft?schema_version=1",
        json={
            "ai_extract_schema": {
                "seller_name": {"type": "string", "description": "The vendor's name."},
                "total": {"type": "number", "description": "The stated total."},
            }
        },
    )
    assert updated.status_code == 200
    published = client.post(f"/api/schemas/{schema_id}/publish?schema_version=1")
    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"
    return published.json()


def _create_and_publish_other_schema(client: TestClient) -> dict:
    """A second, differently-shaped custom schema, so tests can exercise two distinct
    (schema_id, schema_version) pairs against the same document."""
    created = client.post(
        "/api/schemas",
        json={
            "display_name": "Other Custom Extract",
            "root_mode": "SINGLE_RECORD",
            "description": "A second, differently-shaped custom schema.",
        },
    ).json()
    schema_id = created["schema_id"]
    updated = client.put(
        f"/api/schemas/{schema_id}/draft?schema_version=1",
        json={
            "ai_extract_schema": {
                "vendor": {"type": "string", "description": "The vendor's name."},
            }
        },
    )
    assert updated.status_code == 200
    published = client.post(f"/api/schemas/{schema_id}/publish?schema_version=1")
    assert published.status_code == 200
    return published.json()


def _wait_extraction(client: TestClient, document_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/documents/{document_id}/extraction-runs")
        run = response.json()[0]
        if run["status"] != "RUNNING":
            return run
        time.sleep(0.02)
    raise AssertionError("Extraction did not complete")


def test_custom_published_schema_extracts_and_serves_generic_results(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _upload_and_parse(client)
    schema = _create_and_publish_flat_schema(client)

    started = client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": schema["schema_id"], "schema_version": schema["schema_version"]},
    )
    assert started.status_code == 202
    completed = _wait_extraction(client, document_id)
    assert completed["status"] == "EXTRACTED"
    run_id = completed["extraction_run_id"]

    result = client.get(f"/api/extractions/{run_id}")
    assert result.status_code == 200
    payload = result.json()
    assert payload["schema_id"] == schema["schema_id"]
    assert payload["root_mode"] == "SINGLE_RECORD"
    assert payload["result"]["seller_name"]["value"] == "Acme Supplies Ltd"
    assert payload["result"]["total"]["value"] == 114.0

    records = client.get(f"/api/extractions/{run_id}/records")
    assert records.status_code == 200
    records_payload = records.json()
    assert [r["instance_path"] for r in records_payload["records"]] == ["$"]
    fields_by_name = {f["field_name"]: f for f in records_payload["fields"]}
    assert fields_by_name["seller_name"]["value"] == "Acme Supplies Ltd"
    assert fields_by_name["total"]["value"] == 114.0
    assert fields_by_name["total"]["schema_path"] == "total"

    # A run that does not exist yields a clean 404, not a stack trace.
    assert client.get("/api/extractions/does-not-exist").status_code == 404
    assert client.get("/api/extractions/does-not-exist/records").status_code == 404


def test_export_endpoint_produces_a_workbook_for_custom_schema_runs(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _upload_and_parse(client)
    schema = _create_and_publish_flat_schema(client)
    client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": schema["schema_id"], "schema_version": schema["schema_version"]},
    )
    run_id = _wait_extraction(client, document_id)["extraction_run_id"]

    exported = client.post("/api/exports", json={"run_ids": [run_id], "format": "xlsx"})
    assert exported.status_code == 200
    assert exported.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(exported.content) > 0

    csv_bundle = client.post("/api/exports", json={"run_ids": [run_id], "format": "csv"})
    assert csv_bundle.status_code == 200
    assert csv_bundle.headers["content-type"] == "application/zip"


def test_list_extractions_summarizes_runs_for_the_results_page(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _upload_and_parse(client)
    schema = _create_and_publish_flat_schema(client)
    client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": schema["schema_id"], "schema_version": schema["schema_version"]},
    )
    run_id = _wait_extraction(client, document_id)["extraction_run_id"]

    summaries = client.get("/api/extractions").json()
    row = next(s for s in summaries if s["extraction_run_id"] == run_id)
    assert row["document_id"] == document_id
    assert row["document_name"] == "doc.pdf"
    assert row["schema_id"] == schema["schema_id"]
    assert row["schema_version"] == schema["schema_version"]
    assert row["schema_display_name"] == schema["display_name"]
    assert row["status"] == "EXTRACTED"
    assert row["is_latest"] is True
    assert row["records_count"] == 1

    filtered = client.get(f"/api/extractions?schema_id={schema['schema_id']}").json()
    assert filtered and all(s["schema_id"] == schema["schema_id"] for s in filtered)

    filtered_out = client.get("/api/extractions?schema_id=does-not-exist").json()
    assert filtered_out == []


def test_export_of_two_schema_versions_produces_separate_workbooks(tmp_path: Path) -> None:
    client = _client(tmp_path)
    document_id = _upload_and_parse(client)

    schema_a = _create_and_publish_flat_schema(client)
    client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": schema_a["schema_id"], "schema_version": schema_a["schema_version"]},
    )
    run_a = _wait_extraction(client, document_id)["extraction_run_id"]

    schema_b = _create_and_publish_other_schema(client)
    client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": schema_b["schema_id"], "schema_version": schema_b["schema_version"]},
    )
    run_b = _wait_extraction(client, document_id)["extraction_run_id"]

    exported = client.post(
        "/api/exports", json={"run_ids": [run_a, run_b], "format": "xlsx"}
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        names = set(archive.namelist())
        assert names == {
            f"{schema_a['schema_id']}_v{schema_a['schema_version']}.xlsx",
            f"{schema_b['schema_id']}_v{schema_b['schema_version']}.xlsx",
        }

    # A single-schema selection is unaffected: still one plain workbook, not a ZIP.
    single = client.post("/api/exports", json={"run_ids": [run_a], "format": "xlsx"})
    assert single.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_records_are_persisted_on_first_read_and_reused_on_later_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recursive record tree is a write-through cache: the first GET .../records
    computes it via walk_extraction and persists it; every later read (this GET, or the
    Results list) must come from the persisted tables instead of recomputing.
    """
    import idp_app.services.generic_results as generic_results_module

    client = _client(tmp_path)
    document_id = _upload_and_parse(client)
    schema = _create_and_publish_flat_schema(client)
    client.post(
        f"/api/documents/{document_id}/extract",
        json={"schema_id": schema["schema_id"], "schema_version": schema["schema_version"]},
    )
    run_id = _wait_extraction(client, document_id)["extraction_run_id"]

    calls = []
    original_walk = generic_results_module.walk_extraction

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return original_walk(*args, **kwargs)

    monkeypatch.setattr(generic_results_module, "walk_extraction", _spy)

    first = client.get(f"/api/extractions/{run_id}/records")
    assert first.status_code == 200
    assert len(calls) == 1

    database = tmp_path / "idp" / "registry.sqlite3"
    with sqlite3.connect(database) as connection:
        record_rows = connection.execute(
            "SELECT COUNT(*) FROM extracted_records WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        field_rows = connection.execute(
            "SELECT COUNT(*) FROM extracted_fields WHERE extraction_run_id = ? "
            "AND record_id IS NOT NULL",
            (run_id,),
        ).fetchone()[0]
    assert record_rows > 0
    assert field_rows > 0

    second = client.get(f"/api/extractions/{run_id}/records")
    assert second.status_code == 200
    assert second.json() == first.json()
    # The second read must come from the persisted tables, not a second walk_extraction call.
    assert len(calls) == 1

    # The Results list computes the same run's records_count from the persisted cache too,
    # without triggering another walk_extraction call.
    summaries = client.get("/api/extractions").json()
    row = next(s for s in summaries if s["extraction_run_id"] == run_id)
    assert row["records_count"] == 1
    assert len(calls) == 1
