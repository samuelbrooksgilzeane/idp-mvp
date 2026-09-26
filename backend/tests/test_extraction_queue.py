"""Offline queue, worker recovery and API contracts; inference is a stub."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_extraction_batch_requests import create, service

from idp_app.api.dependencies import get_authenticated_user
from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.extraction_execution import execute_extraction
from idp_app.services.extraction_queue import ExtractionQueue
from idp_app.services.extraction_runs import SQLiteExtractionRunRepository
from idp_app.services.parse_jobs import ParseJobPoll, ParseJobState
from idp_app.services.work_batches import SQLiteWorkRepository, changed
from idp_app.services.work_dispatch import WorkDispatcher


class Jobs:
    def __init__(self):
        self.tokens = []
        self.state = ParseJobState.RUNNING
        self.ambiguous = False

    def trigger_manifest(self, dispatch_id):
        self.tokens.append(dispatch_id)
        if self.ambiguous:
            self.ambiguous = False
            raise TimeoutError("response lost")
        return 10

    def poll(self, job_run_id):
        return ParseJobPoll(self.state)


def setup(tmp_path, count=3):
    batches, schema = service(tmp_path, count)
    queue = ExtractionQueue(
        batches,
        SQLiteWorkRepository(tmp_path / "metadata.sqlite3"),
        SQLiteExtractionRunRepository(tmp_path / "metadata.sqlite3"),
    )
    batch = create(batches, schema, [str(index) for index in range(count)])
    jobs = Jobs()
    return queue, batch, jobs, WorkDispatcher(queue, jobs)


def test_bounded_manifests_ambiguous_submission_and_stage_isolation(tmp_path):
    queue, batch, jobs, dispatcher = setup(tmp_path, 1000)
    for _ in range(9):
        assert dispatcher.tick()
        assert not jobs.tokens  # Entire request is validated before publishing work.
    jobs.ambiguous = True
    with pytest.raises(TimeoutError):
        dispatcher.tick()
    assert len(queue.work.active_dispatches("EXTRACT")) == 1
    assert not queue.work.active_dispatches()  # Parsing is an independent stage.
    assert dispatcher.tick()
    assert jobs.tokens[0] == jobs.tokens[1]
    dispatch = queue.work.active_dispatches("EXTRACT")[0]
    assert len(dispatch.work_item_ids) == 100
    assert queue.batches.batches.get(batch.batch_id).published == 100
    item = queue.work.item(dispatch.work_item_ids[0])
    queue.work.update_item(item, changed(item, lease_expires_at="2000-01-01T00:00:00+00:00"))
    dispatcher.tick()
    assert len(jobs.tokens) == 2  # Expiry cannot reclaim an active Job.


def test_retained_result_recovers_without_repeating_inference(tmp_path):
    queue, batch, jobs, dispatcher = setup(tmp_path, 1)
    dispatcher.tick()
    dispatch = queue.work.active_dispatches("EXTRACT")[0]
    identity = dispatch.work_item_ids[0]
    inference = Mock(return_value={"response": {}, "error_message": None})
    original = queue.runs.complete
    with patch.object(queue.runs, "complete", side_effect=TimeoutError("projection unavailable")):
        with pytest.raises(TimeoutError):
            execute_extraction(queue, dispatch.dispatch_id, identity, inference)
    assert inference.call_count == 1
    assert queue.runs.get(queue.work.item(identity).extraction_run_id).ai_result is not None
    jobs.state = ParseJobState.FAILED
    dispatcher.tick()
    assert queue.work.item(identity).state == "SUCCEEDED"
    assert queue.work.batch_counts(batch.batch_id) == {"SUCCEEDED": 1}
    execute_extraction_should_fail = Mock()
    # A finished dispatch cannot start work, even if the caller replays its IDs.
    with pytest.raises(ValueError):
        execute_extraction(queue, dispatch.dispatch_id, identity, execute_extraction_should_fail)
    execute_extraction_should_fail.assert_not_called()
    assert original is not None


def test_failed_job_retries_new_run_but_keeps_pinned_parse(tmp_path):
    queue, batch, jobs, dispatcher = setup(tmp_path, 1)
    dispatcher.tick()
    item = queue.work.batch_items(batch.batch_id)[0]
    jobs.state = ParseJobState.FAILED
    dispatcher.tick()
    retry = queue.work.item(item.work_item_id)
    assert retry.state == "QUEUED" and retry.extraction_run_id != item.extraction_run_id
    assert retry.parse_run_id == item.parse_run_id
    assert queue.runs.get(item.extraction_run_id).status == "FAILED"
    assert retry.next_eligible_at > datetime.now(UTC).isoformat()


def test_worker_rejects_source_change_without_inference(tmp_path):
    queue, batch, jobs, dispatcher = setup(tmp_path, 1)
    dispatcher.tick()
    item = queue.work.batch_items(batch.batch_id)[0]
    document = queue.batches.documents.get(item.document_id)
    with patch.object(
        queue.batches.documents, "get", return_value=replace(document, content_sha256="changed")
    ):
        infer = Mock()
        with pytest.raises(ValueError, match="Pinned"):
            execute_extraction(queue, item.dispatch_id, item.work_item_id, infer)
    infer.assert_not_called()
    assert queue.work.item(item.work_item_id).state == "RUNNING"
    assert queue.runs.get(item.extraction_run_id).status == "FAILED"


def test_table_backed_api_ownership_pagination_and_failed_only_retry(tmp_path):
    queue, batch, jobs, dispatcher = setup(tmp_path, 3)
    dispatcher.tick()
    # Two successes, one permanent failure; no inference involved.
    members = queue.work.batch_items(batch.batch_id)
    for index, item in enumerate(members):
        queue.work.update_item(item, changed(item, state="FAILED" if index == 1 else "SUCCEEDED"))
    app = create_app(
        Settings(_env_file=None, bulk_extraction_enabled=True, local_data_dir=tmp_path)
    )
    app.state.extraction_queue = queue
    app.dependency_overrides[get_authenticated_user] = lambda: "owner"
    with TestClient(app) as client:
        response = client.get(f"/api/extraction-batches/{batch.batch_id}")
        assert response.json()["terminal"]
        page = client.get(f"/api/extraction-batches/{batch.batch_id}/items?limit=2").json()
        assert len(page["items"]) == 2 and page["next_cursor"] == 1
        request_id = str(uuid4())
        retried = client.post(
            f"/api/extraction-batches/{batch.batch_id}/retry",
            json={"client_request_id": request_id},
        )
        assert retried.status_code == 202
        child = queue.batches.batches.get(retried.json()["batch_id"])
        assert child.document_ids == ["1"]
        assert child.retry_inputs["1"]["parse_run_id"] == members[1].parse_run_id
        again = client.post(
            f"/api/extraction-batches/{batch.batch_id}/retry",
            json={"client_request_id": request_id},
        )
        assert again.json()["batch_id"] == child.batch_id
        app.dependency_overrides[get_authenticated_user] = lambda: "someone-else"
        assert client.get(f"/api/extraction-batches/{batch.batch_id}").status_code == 404
    assert len(jobs.tokens) == 1  # Status endpoints never poll or submit Jobs.


def test_api_accepts_thousand_metadata_ids_without_validating_documents(tmp_path):
    queue, _, _, _ = setup(tmp_path, 1)
    schema = queue.batches.batches.pending(1)[0]
    app = create_app(
        Settings(_env_file=None, bulk_extraction_enabled=True, local_data_dir=tmp_path)
    )
    app.state.extraction_queue = queue
    app.dependency_overrides[get_authenticated_user] = lambda: "owner"
    body = {
        "client_request_id": str(uuid4()),
        "document_ids": [str(uuid4()) for _ in range(1000)],
        "schema_id": schema.schema_id,
        "schema_version": schema.schema_version,
    }
    with TestClient(app) as client, patch.object(queue.batches.documents, "get_many") as read:
        response = client.post("/api/extraction-batches", json=body)
        assert response.status_code == 202
        assert response.json()["total"] == 1000 and response.json()["validated"] == 0
        assert (
            client.post("/api/extraction-batches", json=body).json()["batch_id"]
            == response.json()["batch_id"]
        )
        read.assert_not_called()


def test_failure_is_not_terminal_until_central_reconciliation(tmp_path):
    queue, batch, jobs, dispatcher = setup(tmp_path, 1)
    dispatcher.tick()
    item = queue.work.batch_items(batch.batch_id)[0]
    execute_extraction(
        queue,
        item.dispatch_id,
        item.work_item_id,
        lambda *_: {"response": {}, "error_message": "Unsupported source"},
    )
    from idp_app.api.extraction_batches import summary

    assert not summary(queue, queue.batches.batches.get(batch.batch_id))["terminal"]
    jobs.state = ParseJobState.SUCCEEDED
    dispatcher.tick()
    assert queue.work.item(item.work_item_id).state == "FAILED"
    assert summary(queue, queue.batches.batches.get(batch.batch_id))["terminal"]


def test_retry_budget_is_bounded_and_uses_new_run_ids(tmp_path):
    queue, batch, jobs, dispatcher = setup(tmp_path, 1)
    runs = set()
    for attempt in range(3):
        jobs.state = ParseJobState.RUNNING
        dispatcher.tick()
        item = queue.work.batch_items(batch.batch_id)[0]
        runs.add(item.extraction_run_id)
        assert item.attempts == attempt + 1
        jobs.state = ParseJobState.FAILED
        dispatcher.tick()
        item = queue.work.item(item.work_item_id)
        if attempt < 2:
            assert item.state == "QUEUED"
            queue.work.update_item(item, changed(item, next_eligible_at=""))
    assert len(runs) == 3 and item.state == "FAILED"


def test_manifest_job_parameters_contain_only_dispatch_identity():
    from types import SimpleNamespace

    from idp_app.services.extraction_manifest_jobs import DatabricksExtractionManifestJobs

    trigger = Mock(return_value=SimpleNamespace(response=SimpleNamespace(run_id=23)))
    runner = DatabricksExtractionManifestJobs(
        SimpleNamespace(jobs=SimpleNamespace(run_now=trigger)), 9
    )
    assert runner.trigger_manifest("dispatch") == 23
    assert runner.trigger_manifest("dispatch") == 23
    assert trigger.call_args.kwargs == {
        "idempotency_token": "extract-dispatch",
        "job_parameters": {"dispatch_id": "dispatch"},
    }
