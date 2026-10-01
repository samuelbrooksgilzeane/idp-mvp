import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idp_app.core.config import IdpMode, Settings
from idp_app.main import create_app
from idp_app.services.folder_import import FolderImportService, client_file_id, safe_relative

PDF = b"%PDF-1.7\nfolder-import-test"


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(
        create_app(Settings(_env_file=None, local_data_dir=tmp_path, max_upload_batch_files=5))
    ) as client:
        yield client


def import_root(client: TestClient) -> Path:
    return client.app.state.settings.local_data_dir / "import_volume"


def write(client: TestClient, relative: str, content: bytes = PDF) -> Path:
    path = import_root(client) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def inline(client: TestClient) -> FolderImportService:
    """Run imports inside the request, so each test sees the finished outcome."""
    assert client.get("/api/imports/folders").status_code == 200
    service: FolderImportService = client.app.state.folder_import_service
    service.start_run = lambda batch_id, resume: service.run(batch_id)
    return service


def start(client: TestClient, folder: str, key: str = "request-1"):
    return client.post(
        "/api/imports", json={"client_request_id": key, "folder": folder, "case_id": "case-f"}
    )


def items(client: TestClient, batch_id: str) -> dict[str, dict]:
    page = client.get(f"/api/upload-batches/{batch_id}/items", params={"limit": 100}).json()
    return {item["relative_path"]: item for item in page["items"]}


def test_mock_import_runs_in_background_and_empties_the_folder(client: TestClient):
    write(client, "invoices/a.pdf")
    write(client, "invoices/nested/b.pdf", PDF + b"-b")
    notes = write(client, "invoices/notes.txt", b"not a pdf")
    write(client, "invoices/.hidden.pdf", PDF + b"-hidden")
    (import_root(client) / "other").mkdir()

    listing = client.get("/api/imports/folders").json()
    assert listing == {
        "root": import_root(client).as_posix(),
        "folders": [{"name": "invoices"}, {"name": "other"}],
    }
    response = start(client, "invoices")
    assert response.status_code == 201
    batch = response.json()
    assert batch["source"] == "folder" and batch["folder"] == "invoices"
    assert batch["file_count"] == 2 and batch["skipped_files"] == 1
    assert batch["case_id"] == "case-f"
    assert [item["client_file_id"] for item in batch["items"]] == [
        client_file_id("a.pdf"),
        client_file_id("nested/b.pdf"),
    ]

    deadline = time.monotonic() + 20
    summary = {}
    while time.monotonic() < deadline:
        summary = client.get(f"/api/upload-batches/{batch['batch_id']}").json()
        if summary["counts"].get("REGISTERED") == 2:
            break
        time.sleep(0.05)
    assert summary["counts"] == {"REGISTERED": 2}
    assert summary["source"] == "folder"
    documents = client.get("/api/documents").json()
    assert {document["file_name"] for document in documents} == {"a.pdf", "b.pdf"}
    assert {document["case_id"] for document in documents} == {"case-f"}
    assert not (import_root(client) / "invoices" / "a.pdf").exists()
    assert not (import_root(client) / "invoices" / "nested" / "b.pdf").exists()
    assert notes.exists()  # Only imported PDFs are removed.


def test_rerun_and_replayed_start_are_idempotent(client: TestClient):
    service = inline(client)
    write(client, "batch/a.pdf")
    write(client, "batch/b.pdf", PDF + b"-b")
    first = start(client, "batch").json()
    assert first["counts"] == {"QUEUED": 2}  # Outcomes land after the create response.
    assert service.run(first["batch_id"]) == {"REGISTERED": 2}

    # Put a registered file back, as if its deletion had failed: a re-run only removes it.
    leftover = write(client, "batch/a.pdf")
    assert service.run(first["batch_id"]) == {"REGISTERED": 2}
    assert not leftover.exists()
    assert len(client.get("/api/documents").json()) == 2
    # The same request identity names the same batch even though the folder is now empty
    # (a lost response replayed after the Job removed the files), and does not import again.
    assert not any((import_root(client) / "batch").iterdir())
    replay = start(client, "batch").json()
    assert replay["batch_id"] == first["batch_id"]
    assert replay["counts"] == {"REGISTERED": 2}
    assert len(replay["items"]) == 2
    assert len(client.get("/api/documents").json()) == 2
    write(client, "other/x.pdf")
    conflict = start(client, "other")  # Same identity, different folder.
    assert conflict.status_code == 409


def test_partial_failure_keeps_failed_files_and_resume_retries_them(client: TestClient):
    inline(client)
    write(client, "mixed/good.pdf")
    bad = write(client, "mixed/bad.pdf", b"not really a pdf")
    later = write(client, "mixed/later.pdf", PDF + b"-later")
    batch_id = client.post(
        "/api/imports",
        json={"client_request_id": "mixed", "folder": "mixed"},
    ).json()["batch_id"]
    # The create response already ran the import inline; check outcomes per file.
    rows = items(client, batch_id)
    assert rows["good.pdf"]["state"] == "REGISTERED"
    assert rows["bad.pdf"]["state"] == "FAILED"
    assert rows["bad.pdf"]["error_code"] == "UNSUPPORTED_FILE_TYPE"
    assert rows["bad.pdf"]["retryable"] is False
    assert bad.exists()
    assert not later.exists()  # Imported, so removed.

    # A file removed before its run: recorded, retryable, and imported once it is back.
    write(client, "gone/one.pdf", PDF + b"-one")
    gone = write(client, "gone/two.pdf", PDF + b"-two")
    service: FolderImportService = client.app.state.folder_import_service
    service.start_run = lambda batch_id, resume: None
    second = client.post(
        "/api/imports", json={"client_request_id": "gone", "folder": "gone"}
    ).json()
    gone.unlink()
    service.run(second["batch_id"])
    rows = items(client, second["batch_id"])
    assert rows["one.pdf"]["state"] == "REGISTERED"
    assert rows["two.pdf"]["state"] == "FAILED"
    assert rows["two.pdf"]["error_code"] == "IMPORT_FILE_MISSING"
    assert rows["two.pdf"]["retryable"] is True

    write(client, "gone/two.pdf", PDF + b"-two")
    service.start_run = lambda batch_id, resume: service.run(batch_id)
    resumed = client.post(f"/api/imports/{second['batch_id']}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["counts"] == {"REGISTERED": 2}
    # The non-retryable failure is left alone by a resume.
    client.post(f"/api/imports/{batch_id}/resume")
    assert items(client, batch_id)["bad.pdf"]["attempts"] == 1


def test_duplicate_content_resolves_to_the_registered_document(client: TestClient):
    inline(client)
    # Registered earlier through the browser path.
    browser = client.post(
        "/api/documents", files={"files": ("earlier.pdf", PDF, "application/pdf")}
    ).json()["documents"][0]
    write(client, "dupes/copy-of-earlier.pdf")
    write(client, "dupes/x.pdf", PDF + b"-x")
    write(client, "dupes/nested/x-again.pdf", PDF + b"-x")
    batch_id = start(client, "dupes").json()["batch_id"]
    rows = items(client, batch_id)
    assert rows["copy-of-earlier.pdf"]["state"] == "ALREADY_REGISTERED"
    assert rows["copy-of-earlier.pdf"]["document_id"] == browser["document_id"]
    states = {rows["x.pdf"]["state"], rows["nested/x-again.pdf"]["state"]}
    assert states == {"REGISTERED", "ALREADY_REGISTERED"}
    assert rows["x.pdf"]["document_id"] == rows["nested/x-again.pdf"]["document_id"]
    assert len(client.get("/api/documents").json()) == 2
    assert not any(p.is_file() for p in (import_root(client) / "dupes").rglob("*"))


def test_a_file_changed_after_start_is_not_imported(client: TestClient):
    service = inline(client)
    service.start_run = lambda batch_id, resume: None
    write(client, "changing/a.pdf")
    batch_id = start(client, "changing").json()["batch_id"]
    changed = write(client, "changing/a.pdf", PDF + b"-longer")
    service.run(batch_id)
    row = items(client, batch_id)["a.pdf"]
    assert row["state"] == "FAILED"
    assert row["error_code"] == "IMPORT_FILE_CHANGED"
    assert row["retryable"] is False
    assert changed.exists()
    assert client.get("/api/documents").json() == []


@pytest.mark.parametrize("folder", ["missing", "../import_volume", "a/b", ".hidden", "."])
def test_only_listed_folders_can_be_imported(client: TestClient, folder: str):
    write(client, "a/b/x.pdf")
    write(client, ".hidden/x.pdf")
    response = start(client, folder)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "IMPORT_FOLDER_NOT_FOUND"


def test_empty_and_oversized_folders_are_rejected(client: TestClient):
    write(client, "empty/readme.txt", b"no pdfs here")
    response = start(client, "empty")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IMPORT_FOLDER_EMPTY"
    for index in range(6):  # The fixture caps batches at 5 files.
        write(client, f"big/{index}.pdf", PDF + bytes([index]))
    response = start(client, "big", key="big")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TOO_MANY_FILES"


def test_resume_only_applies_to_the_requesters_folder_imports(client: TestClient):
    inline(client)
    browser_batch = client.post(
        "/api/upload-batches",
        json={
            "client_request_id": "browser",
            "files": [{"client_file_id": "f", "name": "a.pdf", "size": len(PDF)}],
        },
    ).json()
    assert client.post(f"/api/imports/{browser_batch['batch_id']}/resume").status_code == 404
    write(client, "mine/a.pdf")
    batch_id = start(client, "mine").json()["batch_id"]
    other = client.post(
        f"/api/imports/{batch_id}/resume", headers={"x-forwarded-email": "someone@else"}
    )
    assert other.status_code == 404


def test_paths_cannot_leave_the_import_root():
    assert safe_relative("f", "a/b.pdf").as_posix() == "f/a/b.pdf"
    for folder, relative in [
        ("..", ""),
        ("a/b", ""),
        ("", ""),
        ("f", "../x.pdf"),
        ("f", "/etc/x.pdf"),
        ("f", "a/../../x.pdf"),
        ("f", "a\\..\\x.pdf"),
    ]:
        with pytest.raises(ValueError):
            safe_relative(folder, relative)


def test_databricks_mode_enables_folder_import_only_when_fully_configured():
    base = {
        "_env_file": None,
        "mode": IdpMode.DATABRICKS,
        "catalog": "c",
        "project_schema": "s",
        "table_prefix": "p",
        "source_volume_name": "src",
        "artifacts_volume_name": "art",
        "warehouse_id": "w",
        "parse_job_id": 1,
        "extraction_job_id": 2,
    }
    assert Settings(**base).folder_import_enabled is False
    assert Settings(**base, import_volume_name="idp_import", import_job_id=3).folder_import_enabled
    with pytest.raises(ValueError, match="IDP_IMPORT_JOB_ID"):
        Settings(**base, import_volume_name="idp_import")
    with pytest.raises(ValueError):
        Settings(**base, import_volume_name="../escape", import_job_id=3)


def test_databricks_source_lists_recursively_and_runner_uses_stable_tokens():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from idp_app.services.folder_import import DatabricksImportJobRunner, DatabricksImportSource

    root = "/Volumes/c/s/idp_import"
    tree = {
        root: [
            SimpleNamespace(path=f"{root}/inv/", name="inv", is_directory=True, file_size=None),
            SimpleNamespace(path=f"{root}/.tmp/", name=".tmp", is_directory=True, file_size=None),
            SimpleNamespace(path=f"{root}/x.pdf", name="x.pdf", is_directory=False, file_size=1),
        ],
        f"{root}/inv": [
            SimpleNamespace(
                path=f"{root}/inv/a.pdf", name="a.pdf", is_directory=False, file_size=9
            ),
            SimpleNamespace(path=f"{root}/inv/sub/", name="sub", is_directory=True, file_size=None),
        ],
        f"{root}/inv/sub": [
            SimpleNamespace(
                path=f"{root}/inv/sub/b.PDF", name="b.PDF", is_directory=False, file_size=7
            ),
        ],
    }
    client = MagicMock()
    client.files.list_directory_contents.side_effect = lambda path, page_size: iter(tree[path])
    source = DatabricksImportSource(client, "c", "s", "idp_import")
    assert source.list_folders() == ["inv"]
    assert sorted((e.relative_path, e.size) for e in source.list_files("inv")) == [
        ("a.pdf", 9),
        ("sub/b.PDF", 7),
    ]
    source.delete("inv", "sub/b.PDF")
    client.files.delete.assert_called_once_with(f"{root}/inv/sub/b.PDF")

    runner = DatabricksImportJobRunner(client, 42)
    runner("batch-1", False)
    runner("batch-1", False)
    first, second = client.jobs.run_now.call_args_list
    assert first == second  # A replayed start cannot start a second run.
    assert first.kwargs == {
        "job_parameters": {"batch_id": "batch-1"},
        "idempotency_token": "import-batch-1",
    }
    runner("batch-1", True)
    assert client.jobs.run_now.call_args.kwargs["idempotency_token"].startswith("import-batch-1-")
