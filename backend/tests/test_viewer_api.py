import json
import sqlite3
import time
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.parse_runs import SQLiteParseRunRepository
from idp_app.services.viewer import normalise_box


def _pdf_bytes(text: str = "Invoice 5814\nService fee GBP 842.75") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


def _client(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = Settings(_env_file=None, local_data_dir=tmp_path / "idp")
    return TestClient(create_app(settings)), settings


def _upload_and_parse(
    client: TestClient,
    content: bytes,
    name: str,
) -> tuple[dict, dict]:
    upload = client.post(
        "/api/documents",
        files=[("files", (name, content, "application/pdf"))],
    )
    assert upload.status_code == 201
    document = upload.json()["documents"][0]
    started = client.post(f"/api/documents/{document['document_id']}/parse")
    assert started.status_code == 202
    parse_run_id = started.json()["parse_run_id"]
    for _ in range(100):
        run = client.get(f"/api/runs/{parse_run_id}").json()
        if run["status"] != "RUNNING":
            assert run["status"] == "SUCCESS"
            return document, run
        time.sleep(0.02)
    raise AssertionError("Parse run did not finish")


def test_page_metadata_elements_and_image_stream_are_document_scoped(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    document, _ = _upload_and_parse(client, _pdf_bytes(), "viewer-invoice.pdf")
    document_id = document["document_id"]

    pages_response = client.get(f"/api/documents/{document_id}/pages")

    assert pages_response.status_code == 200
    pages = pages_response.json()
    assert pages == [
        {
            "page_id": 0,
            "page_number": 1,
            "element_count": 1,
            "element_types": ["text"],
            "image_url": f"/api/documents/{document_id}/pages/0/image",
        }
    ]
    assert "/Volumes/" not in pages_response.text
    assert str(tmp_path) not in pages_response.text

    elements_response = client.get(
        f"/api/documents/{document_id}/elements?page_id=0&type=text"
    )
    assert elements_response.status_code == 200
    elements = elements_response.json()
    assert len(elements) == 1
    assert elements[0]["element_type"] == "text"
    assert "Invoice 5814" in elements[0]["content"]
    assert elements[0]["boxes"] == [
        {
            "page_id": 0,
            "x": 108.0,
            "y": 90.0,
            "width": 177.0,
            "height": 46.0,
        }
    ]
    assert client.get(
        f"/api/documents/{document_id}/elements?page_id=0&type=table"
    ).json() == []

    image_response = client.get(pages[0]["image_url"])
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.headers["cache-control"] == "private, max-age=300"
    assert image_response.content.startswith(b"\x89PNG")


def test_unparsed_missing_page_and_missing_image_states(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    upload = client.post(
        "/api/documents",
        files=[("files", ("waiting.pdf", _pdf_bytes("Waiting"), "application/pdf"))],
    ).json()["documents"][0]

    unparsed = client.get(f"/api/documents/{upload['document_id']}/pages")
    assert unparsed.status_code == 409
    assert unparsed.json()["error"]["code"] == "DOCUMENT_NOT_PARSED"

    document, run = _upload_and_parse(
        client,
        _pdf_bytes("Missing page image"),
        "missing-image.pdf",
    )
    missing_page = client.get(f"/api/documents/{document['document_id']}/pages/9/image")
    assert missing_page.status_code == 404
    assert missing_page.json()["error"]["code"] == "PAGE_NOT_FOUND"

    repository = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    retained = repository.get(run["parse_run_id"])
    assert retained is not None and retained.parsed is not None
    image_path = Path(retained.parsed["document"]["pages"][0]["image_uri"])
    image_path.unlink()
    missing_image = client.get(
        f"/api/documents/{document['document_id']}/pages/0/image"
    )
    assert missing_image.status_code == 404
    assert missing_image.json()["error"]["code"] == "PAGE_IMAGE_MISSING"


def test_page_image_cannot_cross_parse_run_boundary(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    first_document, first_run = _upload_and_parse(
        client,
        _pdf_bytes("First document"),
        "first.pdf",
    )
    _, second_run = _upload_and_parse(
        client,
        _pdf_bytes("Second document"),
        "second.pdf",
    )
    repository = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    second = repository.get(second_run["parse_run_id"])
    assert second is not None and second.parsed is not None
    foreign_image = second.parsed["document"]["pages"][0]["image_uri"]

    database_path = settings.local_data_dir / "registry.sqlite3"
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT parsed FROM parse_runs WHERE parse_run_id = ?",
            (first_run["parse_run_id"],),
        ).fetchone()
        assert row is not None
        parsed = json.loads(row[0])
        parsed["document"]["pages"][0]["image_uri"] = foreign_image
        connection.execute(
            "UPDATE parse_runs SET parsed = ? WHERE parse_run_id = ?",
            (json.dumps(parsed), first_run["parse_run_id"]),
        )

    response = client.get(
        f"/api/documents/{first_document['document_id']}/pages/0/image"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAGE_IMAGE_INVALID"


def test_viewer_can_be_scoped_to_the_parse_run_used_by_an_extraction(
    tmp_path: Path,
) -> None:
    client, settings = _client(tmp_path)
    document, first_run = _upload_and_parse(
        client,
        _pdf_bytes("Historical parse"),
        "historical.pdf",
    )
    document_id = document["document_id"]

    repository = SQLiteParseRunRepository(settings.local_data_dir / "registry.sqlite3")
    retained = repository.get(first_run["parse_run_id"])
    assert retained is not None and retained.parsed is not None
    retained.parsed["document"]["pages"][0]["id"] = 7
    database_path = settings.local_data_dir / "registry.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE parse_runs SET parsed = ? WHERE parse_run_id = ?",
            (json.dumps(retained.parsed), first_run["parse_run_id"]),
        )

    second = client.post(f"/api/documents/{document_id}/parse")
    assert second.status_code == 202
    second_run_id = second.json()["parse_run_id"]
    for _ in range(100):
        run = client.get(f"/api/runs/{second_run_id}").json()
        if run["status"] != "RUNNING":
            assert run["status"] == "SUCCESS"
            break
        time.sleep(0.02)
    else:
        raise AssertionError("Second parse run did not finish")

    latest_pages = client.get(f"/api/documents/{document_id}/pages").json()
    historical_pages = client.get(
        f"/api/documents/{document_id}/pages",
        params={"parse_run_id": first_run["parse_run_id"]},
    ).json()

    assert latest_pages[0]["page_id"] == 0
    assert historical_pages[0]["page_id"] == 7
    assert historical_pages[0]["image_url"].endswith(
        f"?parse_run_id={first_run['parse_run_id']}"
    )
    assert client.get(historical_pages[0]["image_url"]).status_code == 200

    foreign_document, foreign_run = _upload_and_parse(
        client,
        _pdf_bytes("Foreign parse"),
        "foreign.pdf",
    )
    foreign_response = client.get(
        f"/api/documents/{foreign_document['document_id']}/pages",
        params={"parse_run_id": first_run["parse_run_id"]},
    )
    assert foreign_response.status_code == 404
    assert foreign_response.json()["error"]["code"] == "PARSE_RUN_NOT_FOUND"


def test_bounding_boxes_normalise_databricks_rectangles_and_mock_polygons() -> None:
    rectangle = normalise_box({"page_id": 0, "coord": [17, 850, 1425, 1310]})
    polygon = normalise_box(
        {"page_id": 2, "coord": [10, 20, 90, 20, 90, 70, 10, 70]}
    )

    assert rectangle is not None
    assert (rectangle.x, rectangle.y, rectangle.width, rectangle.height) == (
        17,
        850,
        1408,
        460,
    )
    assert polygon is not None
    assert (polygon.page_id, polygon.x, polygon.y, polygon.width, polygon.height) == (
        2,
        10,
        20,
        80,
        50,
    )
