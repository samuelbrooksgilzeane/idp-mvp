from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import pytest
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.document_models import DocumentRecord
from idp_app.services.documents import DocumentService

PDF_ONE = b"%PDF-1.7\n1 0 obj<</Type/Catalog>>endobj\n%%EOF"
PDF_TWO = b"%PDF-1.7\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None, local_data_dir=tmp_path / "local"))
    with TestClient(app) as test_client:
        yield test_client


def upload_file(
    client: TestClient,
    *,
    filename: str = "invoice.pdf",
    content: bytes = PDF_ONE,
    content_type: str = "application/pdf",
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
):
    return client.post(
        "/api/documents",
        files={"files": (filename, content, content_type)},
        headers=headers,
        data=data,
    )


def test_valid_pdf_upload_is_registered_and_retrievable(client: TestClient) -> None:
    response = upload_file(
        client,
        headers={"x-forwarded-email": "analyst@example.com"},
        data={"case_id": "case-42", "template_id": "invoice_v1", "use_case": "invoice"},
    )

    assert response.status_code == 201
    document = response.json()["documents"][0]
    assert document["file_name"] == "invoice.pdf"
    assert document["status"] == "UPLOADED"
    assert document["uploaded_by"] == "analyst@example.com"
    assert document["case_id"] == "case-42"
    assert "source_path" not in document

    detail = client.get(f"/api/documents/{document['document_id']}")
    assert detail.status_code == 200
    assert detail.json() == document


def test_multiple_pdf_upload_returns_each_registered_document(client: TestClient) -> None:
    response = client.post(
        "/api/documents",
        files=[
            ("files", ("one.pdf", PDF_ONE, "application/pdf")),
            ("files", ("two.pdf", PDF_TWO, "application/pdf")),
        ],
    )

    assert response.status_code == 201
    assert [item["file_name"] for item in response.json()["documents"]] == [
        "one.pdf",
        "two.pdf",
    ]
    assert len(client.get("/api/documents").json()) == 2


@pytest.mark.parametrize(
    ("filename", "content", "content_type"),
    [
        ("invoice.txt", PDF_ONE, "application/pdf"),
        ("invoice.pdf", PDF_ONE, "text/plain"),
        ("invoice.pdf", b"not a PDF", "application/pdf"),
    ],
)
def test_non_pdf_inputs_are_rejected(
    client: TestClient,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    response = upload_file(
        client,
        filename=filename,
        content=content,
        content_type=content_type,
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert client.get("/api/documents").json() == []


def test_oversized_pdf_is_rejected(tmp_path: Path) -> None:
    app = create_app(
        Settings(_env_file=None, local_data_dir=tmp_path / "local", max_upload_bytes=8)
    )
    client = TestClient(app)

    response = upload_file(client, content=PDF_ONE)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_path_traversal_filename_is_sanitized_and_storage_path_is_server_owned(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = upload_file(
        client,
        filename="../../client invoice.pdf",
        data={"storage_path": "/Volumes/attacker/schema/volume/file.pdf"},
    )

    assert response.status_code == 201
    document = response.json()["documents"][0]
    assert document["file_name"] == "client_invoice.pdf"
    stored_files = list((tmp_path / "local" / "source_volume" / "incoming").iterdir())
    assert stored_files == [
        tmp_path
        / "local"
        / "source_volume"
        / "incoming"
        / f"{document['document_id']}.pdf"
    ]


def test_long_filename_keeps_pdf_extension(client: TestClient) -> None:
    response = upload_file(client, filename=f"{'a' * 300}.pdf")

    assert response.status_code == 201
    file_name = response.json()["documents"][0]["file_name"]
    assert len(file_name) == 255
    assert file_name.endswith(".pdf")


@pytest.mark.parametrize("second_name", ["invoice.pdf", "renamed.pdf"])
def test_duplicate_content_is_deterministic(client: TestClient, second_name: str) -> None:
    first = upload_file(client)
    duplicate = upload_file(client, filename=second_name)

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == {
        "code": "DOCUMENT_DUPLICATE",
        "message": "This PDF is already registered as invoice.pdf.",
        "document_id": first.json()["documents"][0]["document_id"],
    }
    assert len(client.get("/api/documents").json()) == 1


def test_storage_failure_never_reports_upload_success(tmp_path: Path) -> None:
    registry = InMemoryRegistry()
    service = DocumentService(FailingStorage(), registry, 1024)
    app = create_app(Settings(_env_file=None, local_data_dir=tmp_path))
    app.state.document_service = service
    client = TestClient(app)

    response = upload_file(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "FILE_STORAGE_FAILED"
    assert registry.documents == []


def test_registry_failure_reports_partial_failure(tmp_path: Path) -> None:
    storage = InMemoryStorage()
    service = DocumentService(storage, FailingRegistry(), 1024)
    app = create_app(Settings(_env_file=None, local_data_dir=tmp_path))
    app.state.document_service = service
    client = TestClient(app)

    response = upload_file(client)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "REGISTRY_WRITE_FAILED"
    assert len(storage.objects) == 1


def test_multi_file_partial_failure_is_explicit(client: TestClient) -> None:
    response = client.post(
        "/api/documents",
        files=[
            ("files", ("one.pdf", PDF_ONE, "application/pdf")),
            ("files", ("bad.txt", b"bad", "text/plain")),
        ],
    )

    assert response.status_code == 207
    assert [item["file_name"] for item in response.json()["documents"]] == ["one.pdf"]
    assert response.json()["errors"][0]["code"] == "UNSUPPORTED_FILE_TYPE"


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def store(self, object_name: str, contents: BinaryIO) -> str:
        contents.seek(0)
        self.objects[object_name] = contents.read()
        return f"memory://incoming/{object_name}"


class FailingStorage:
    def store(self, object_name: str, contents: BinaryIO) -> str:
        del object_name, contents
        raise RuntimeError("files API unavailable")


class InMemoryRegistry:
    def __init__(self) -> None:
        self.documents: list[DocumentRecord] = []

    def find_by_hash(self, content_sha256: str) -> DocumentRecord | None:
        return next(
            (
                document
                for document in self.documents
                if document.content_sha256 == content_sha256
            ),
            None,
        )

    def add(self, document: DocumentRecord) -> None:
        self.documents.append(document)

    def list_documents(self) -> list[DocumentRecord]:
        return list(self.documents)

    def get(self, document_id: str) -> DocumentRecord | None:
        return next(
            (
                document
                for document in self.documents
                if document.document_id == document_id
            ),
            None,
        )


class FailingRegistry(InMemoryRegistry):
    def add(self, document: DocumentRecord) -> None:
        del document
        raise RuntimeError("registry unavailable")
