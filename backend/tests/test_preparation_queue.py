from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.document_models import DocumentRecord
from idp_app.services.document_registry import SQLiteDocumentRegistry
from idp_app.services.parse_jobs import ParseJobPoll, ParseJobState
from idp_app.services.parse_runs import SQLiteParseRunRepository
from idp_app.services.preparation import PreparationService, initial_work_id
from idp_app.services.work_batches import SQLiteWorkRepository, changed
from idp_app.services.work_dispatch import WorkDispatcher, manifest_ids
from idp_app.services.work_execution import begin_parse


class FakeJobs:
    def __init__(self):
        self.tokens = []
        self.fail_submission = False
        self.state = ParseJobState.RUNNING
        self.polls = 0

    def trigger_manifest(self, dispatch_id):
        self.tokens.append(dispatch_id)
        if self.fail_submission:
            self.fail_submission = False
            raise TimeoutError("Ambiguous submission")
        return 42

    def poll(self, job_run_id):
        self.polls += 1
        return ParseJobPoll(self.state)


@pytest.fixture
def setup(tmp_path: Path):
    settings = Settings(_env_file=None, local_data_dir=tmp_path, auto_prepare_enabled=True)
    documents = SQLiteDocumentRegistry(tmp_path / "registry.sqlite3")
    runs = SQLiteParseRunRepository(tmp_path / "registry.sqlite3")
    work = SQLiteWorkRepository(tmp_path / "registry.sqlite3")
    preparation = PreparationService(settings, documents, runs, work)
    jobs = FakeJobs()
    return preparation, jobs


def document(preparation, index=0):
    now = datetime.now(UTC)
    row = DocumentRecord(
        f"doc-{index}",
        None,
        "invoice_v1",
        "invoice",
        "unused.pdf",
        f"document-{index}.pdf",
        1,
        f"hash-{index}",
        None,
        None,
        "UPLOADED",
        "tester",
        now,
        now,
    )
    preparation.documents.add(row)
    return row


def test_upload_enqueues_without_calling_ai_or_jobs(tmp_path: Path):
    settings = Settings(_env_file=None, local_data_dir=tmp_path, auto_prepare_enabled=True)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/documents", files={"files": ("small.pdf", b"%PDF-local", "application/pdf")}
        )
        assert response.status_code == 201
        row = response.json()["documents"][0]
        assert row["status"] == "PARSE_QUEUED"
        first = client.post(f"/api/documents/{row['document_id']}/parse")
        again = client.post(f"/api/documents/{row['document_id']}/parse")
        assert first.status_code == 202
        assert first.json()["status"] == "QUEUED"
        assert again.json()["parse_run_id"] == first.json()["parse_run_id"]
        assert client.get("/api/preparation").json()["counts"] == {"QUEUED": 1}


def test_metadata_backlog_is_dispatched_in_bounded_manifests(setup):
    preparation, jobs = setup
    for index in range(1000):
        document(preparation, index)
    dispatcher = WorkDispatcher(preparation, jobs)
    assert dispatcher.tick()
    active = preparation.work.active_dispatches()
    assert len(active) == 1
    assert len(manifest_ids(active[0], preparation.work)) == 100
    assert len(jobs.tokens) == 1
    assert dispatcher.tick()
    assert len(jobs.tokens) == 1  # One active dispatch, not a Job for every file.
    assert jobs.polls == 2


def test_ambiguous_submission_reuses_dispatch_after_restart(setup):
    preparation, jobs = setup
    row = document(preparation)
    run = preparation.request(row.document_id, "tester")
    jobs.fail_submission = True
    with pytest.raises(TimeoutError):
        WorkDispatcher(preparation, jobs).tick()
    assert preparation.work.active_dispatches()[0].state == "SUBMITTING"
    WorkDispatcher(preparation, jobs).tick()
    assert jobs.tokens[0] == jobs.tokens[1]
    assert preparation.runs.get(run.parse_run_id).job_run_id == 42


def test_task_claim_is_consumed_once_and_active_job_is_never_reclaimed(setup):
    preparation, jobs = setup
    row = document(preparation)
    preparation.request(row.document_id, "tester")
    dispatcher = WorkDispatcher(preparation, jobs)
    dispatcher.tick()
    dispatch = preparation.work.active_dispatches()[0]
    identity = initial_work_id(row)
    claimed = begin_parse(preparation, dispatch.dispatch_id, identity)
    assert claimed is not None
    with pytest.raises(ValueError, match="unconsumed claim"):
        begin_parse(preparation, dispatch.dispatch_id, identity)
    old = preparation.work.item(identity)
    preparation.work.update_item(old, changed(old, lease_expires_at="2000-01-01T00:00:00+00:00"))
    dispatcher.tick()
    assert preparation.work.item(identity).state == "RUNNING"
    assert len(jobs.tokens) == 1


def test_retained_result_is_completed_without_new_inference_after_crash(setup):
    preparation, jobs = setup
    row = document(preparation)
    run = preparation.request(row.document_id, "tester")
    dispatcher = WorkDispatcher(preparation, jobs)
    dispatcher.tick()
    dispatch = preparation.work.active_dispatches()[0]
    begin_parse(preparation, dispatch.dispatch_id, initial_work_id(row))
    # Simulate the task persisting raw inference and crashing before its projection commit.
    with preparation.runs._connect() as connection:
        connection.execute(
            "UPDATE parse_runs SET parsed = ? WHERE parse_run_id = ?",
            (
                '{"document":{"pages":[{"id":0}],"elements":[{"content":"retained"}]},"error_status":[]}',
                run.parse_run_id,
            ),
        )
    jobs.state = ParseJobState.FAILED
    dispatcher.tick()
    assert preparation.runs.get(run.parse_run_id).status == "SUCCESS"
    assert preparation.runs.get(run.parse_run_id).document_text == "retained"
    assert preparation.work.item(initial_work_id(row)).state == "SUCCEEDED"
    assert preparation.documents.get(row.document_id).status == "PARSED"
    assert len(jobs.tokens) == 1


def test_transport_failure_gets_new_immutable_attempt_and_bounded_retry(setup):
    preparation, jobs = setup
    row = document(preparation)
    original = preparation.request(row.document_id, "tester")
    dispatcher = WorkDispatcher(preparation, jobs, max_attempts=2)
    dispatcher.tick()
    jobs.state = ParseJobState.FAILED
    dispatcher.tick()
    item = preparation.work.item(initial_work_id(row))
    assert item.state == "QUEUED"
    assert item.parse_run_id != original.parse_run_id
    assert preparation.runs.get(original.parse_run_id).status == "FAILED"
    preparation.work.update_item(item, changed(item, next_eligible_at="2000-01-01T00:00:00+00:00"))
    dispatcher.tick()
    assert preparation.work.item(initial_work_id(row)).state == "FAILED"
    assert len(jobs.tokens) == 2


def test_explicit_reparse_preserves_successful_run(setup):
    preparation, jobs = setup
    row = document(preparation)
    original = preparation.request(row.document_id, "tester")
    dispatcher = WorkDispatcher(preparation, jobs)
    dispatcher.tick()
    active = preparation.work.active_dispatches()[0]
    begin_parse(preparation, active.dispatch_id, initial_work_id(row))
    preparation.runs.complete(original.parse_run_id, {"document": {}, "error_status": []}, "", 0)
    jobs.state = ParseJobState.SUCCEEDED
    dispatcher.tick()
    assert preparation.request(row.document_id, "tester").parse_run_id == original.parse_run_id
    new = preparation.request(row.document_id, "tester", reparse=True)
    assert new.parse_run_id != original.parse_run_id
    assert new.status == "QUEUED"
    assert preparation.runs.get(original.parse_run_id).status == "SUCCESS"
