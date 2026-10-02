"""Extraction ownership: one run owns a document's status (claim, completion, validation)."""

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from databricks.sdk.errors import BadRequest
from fastapi.testclient import TestClient
from test_extraction_api import _client, _upload, _wait_extraction, _wait_parse  # noqa: F401

from idp_app.main import create_app
from idp_app.services.document_registry import (
    DatabricksDocumentRegistry,
    InvalidDocumentStateError,
    SQLiteDocumentRegistry,
)
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction import ExtractionService
from idp_app.services.extraction_inputs import ELIGIBLE_DOCUMENT_STATES
from idp_app.services.extraction_jobs import (
    ExtractionJobPoll,
    ExtractionJobState,
    MockExtractionJobRunner,
)
from idp_app.services.extraction_runs import SQLiteExtractionRunRepository
from idp_app.services.parse_runs import SQLiteParseRunRepository
from idp_app.services.schema_registry import SQLiteSchemaRepository
from idp_app.services.schemas import load_source_manifests
from idp_app.services.sql_retry import SqlOutcomeUnknownError


@pytest.fixture
def parsed(tmp_path: Path):
    client, settings = _client(tmp_path)
    document_id = _upload(client)["document_id"]
    _wait_parse(client, document_id)
    database = settings.local_data_dir / "registry.sqlite3"
    return client, settings, document_id, database


def no_wait(_: float) -> None:
    """Retry backoff without waiting; the module's own `time` is replaced, not the shared one."""


def _service(database: Path, *, runs=None, jobs=None) -> ExtractionService:
    documents = SQLiteDocumentRegistry(database)
    runs = runs or SQLiteExtractionRunRepository(database)
    parse_runs = SQLiteParseRunRepository(database)
    jobs = jobs or MockExtractionJobRunner(runs, documents, delay_seconds=0, parse_runs=parse_runs)
    schemas = SQLiteSchemaRepository(database)
    schemas.register(load_source_manifests()[0], "test")
    return ExtractionService(documents, parse_runs, schemas, runs, jobs)


# I5: the claim has exactly one owner.


@pytest.mark.parametrize("second_schema", [("invoice", 1), ("invoice", 2)])
def test_two_claimants_produce_exactly_one_owner(parsed, second_schema) -> None:
    _, _, document_id, database = parsed
    documents = SQLiteDocumentRegistry(database)
    won = documents.begin_extraction(document_id, ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "run-a")
    assert won.extraction_run_id == "run-a"
    with pytest.raises(InvalidDocumentStateError):
        documents.begin_extraction(document_id, ELIGIBLE_DOCUMENT_STATES, *second_schema, "run-b")
    assert documents.get(document_id).extraction_run_id == "run-a"  # type: ignore[union-attr]
    # A retry by the owner after a lost answer is recognized by its token.
    again = documents.begin_extraction(
        document_id, ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "run-a"
    )
    assert again.extraction_run_id == "run-a"


def test_stale_completion_cannot_overwrite_a_newer_owner(parsed) -> None:
    _, _, document_id, database = parsed
    documents = SQLiteDocumentRegistry(database)
    documents.begin_extraction(document_id, ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "old")
    documents.update_status(document_id, {"EXTRACTING"}, "EXTRACT_FAILED", "old")
    documents.begin_extraction(document_id, ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "new")
    with pytest.raises(InvalidDocumentStateError):
        documents.update_status(document_id, {"EXTRACTING"}, "EXTRACTED", "old")
    current = documents.get(document_id)
    assert current is not None
    assert (current.status, current.extraction_run_id) == ("EXTRACTING", "new")


def test_databricks_claim_that_lost_the_race_is_not_reported_as_won() -> None:
    """The review's reproduction: the update matched no row, and the read-back shows another
    request's claim (even for a different schema)."""
    registry = DatabricksDocumentRegistry(Mock(), "wh", "c", "s", "idp")
    registry.execute_sql = Mock(return_value=[])  # type: ignore[method-assign]
    winner = Mock(status="EXTRACTING", extraction_run_id="other-run")
    registry.get = Mock(return_value=winner)  # type: ignore[method-assign]
    with pytest.raises(InvalidDocumentStateError):
        registry.begin_extraction("doc", ELIGIBLE_DOCUMENT_STATES, "invoice", 2, "my-run")
    statement = registry.execute_sql.call_args.args[0]
    assert "extraction_run_id = :extraction_run_id" in statement
    mine = Mock(status="EXTRACTING", extraction_run_id="my-run")
    registry.get = Mock(return_value=mine)  # type: ignore[method-assign]
    assert registry.begin_extraction("doc", ELIGIBLE_DOCUMENT_STATES, "invoice", 2, "my-run")


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("[DELTA_CONCURRENT_APPEND.ROW_LEVEL_CHANGES] Transaction conflict detected"),
        SqlOutcomeUnknownError("passed its deadline; it may still have committed"),
    ],
)
def test_databricks_claim_conflict_or_unknown_outcome_is_settled_by_its_token(failure) -> None:
    registry = DatabricksDocumentRegistry(Mock(), "wh", "c", "s", "idp")
    registry.execute_sql = Mock(side_effect=failure)  # type: ignore[method-assign]
    registry.get = Mock(return_value=Mock(status="EXTRACTING", extraction_run_id="winner"))  # type: ignore[method-assign]
    with pytest.raises(InvalidDocumentStateError):  # busy, not a 500
        registry.begin_extraction("doc", ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "loser")
    assert registry.begin_extraction("doc", ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "winner")
    registry.execute_sql = Mock(side_effect=RuntimeError("syntax error"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="syntax"):
        registry.begin_extraction("doc", ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "winner")


# I2: validation publishes only the owning extraction's outcome.


def test_validating_an_older_extraction_keeps_the_current_status(parsed) -> None:
    client, _, document_id, database = parsed
    runs = []
    for _ in range(2):
        response = client.post(
            f"/api/documents/{document_id}/extract",
            json={"schema_id": "invoice", "schema_version": 1},
        )
        runs.append(response.json()["extraction_run_id"])
        assert _wait_extraction(client, document_id)["status"] == "EXTRACTED"
    older, newer = runs
    documents = SQLiteDocumentRegistry(database)
    assert documents.get(document_id).extraction_run_id == newer  # type: ignore[union-attr]

    current = client.post(f"/api/documents/{document_id}/validate", json={})
    assert current.status_code == 201
    validated = documents.get(document_id).status  # type: ignore[union-attr]
    assert validated in {"VALIDATED_PASS", "REVIEW_REQUIRED"}
    # Mark the overview so any write by the historical validation would show.
    documents.update_status(document_id, {validated}, "EXTRACTED", newer)

    historical = client.post(
        f"/api/documents/{document_id}/validate", json={"extraction_run_id": older}
    )
    assert historical.status_code == 201  # The report is still kept...
    assert historical.json()["run"]["extraction_run_id"] == older
    published = documents.get(document_id)
    assert published is not None and published.status == "EXTRACTED"  # ...not published


# I3: a claim is never stranded without a way to settle it.


def test_failed_run_creation_releases_the_claim(parsed) -> None:
    _, _, document_id, database = parsed

    class FailingCreate(SQLiteExtractionRunRepository):
        def create(self, run):  # noqa: ANN001
            raise TimeoutError("run write timed out")

    service = _service(database, runs=FailingCreate(database))
    with pytest.raises(TimeoutError):
        asyncio.run(service.start(document_id, "invoice", 1, "ann"))
    document = SQLiteDocumentRegistry(database).get(document_id)
    assert document is not None and document.status == "EXTRACT_FAILED"
    # Nothing is stuck: the next request extracts.
    run = asyncio.run(_service(database).start(document_id, "invoice", 1, "ann"))
    assert run.status == "RUNNING"


def test_a_claim_left_by_a_stopped_process_is_recovered_by_the_next_request(parsed) -> None:
    _, _, document_id, database = parsed
    documents = SQLiteDocumentRegistry(database)
    # The process stopped after claiming, before writing its run row.
    documents.begin_extraction(document_id, ELIGIBLE_DOCUMENT_STATES, "invoice", 1, "lost-run")
    service = _service(database)
    with pytest.raises(DocumentServiceError, match="EXTRACTING"):
        asyncio.run(service.start(document_id, "invoice", 1, "ann"))  # still within grace

    import sqlite3

    stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE documents SET updated_at = ?", (stale,))
    run = asyncio.run(service.start(document_id, "invoice", 1, "ann"))
    assert documents.get(document_id).extraction_run_id == run.extraction_run_id  # type: ignore[union-attr]


class IdleRunner:
    """Accepts submissions but never runs them, so the test decides each run's outcome."""

    def trigger(self, requests):  # noqa: ANN001
        return 7

    def poll(self, job_run_id):  # noqa: ANN001
        return ExtractionJobPoll(ExtractionJobState.RUNNING)

    def find(self, extraction_run_id, since):  # noqa: ANN001
        return 7


def test_a_claim_whose_run_already_finished_is_settled_from_that_run(parsed) -> None:
    _, _, document_id, database = parsed
    service = _service(database, jobs=IdleRunner())
    first = asyncio.run(service.start(document_id, "invoice", 1, "ann"))
    runs = SQLiteExtractionRunRepository(database)
    runs.fail(first.extraction_run_id, "job failed")  # finished; the job never released it
    documents = SQLiteDocumentRegistry(database)
    with __import__("sqlite3").connect(database) as connection:
        connection.execute("UPDATE documents SET status = 'EXTRACTING'")
    second = asyncio.run(service.start(document_id, "invoice", 1, "ann"))
    assert second.extraction_run_id != first.extraction_run_id
    assert documents.get(document_id).extraction_run_id == second.extraction_run_id  # type: ignore[union-attr]


# I4: an accepted job is never rolled back as though submission failed.


def test_accepted_job_whose_id_was_not_recorded_still_commits(parsed, monkeypatch) -> None:
    _, _, document_id, database = parsed
    monkeypatch.setattr("idp_app.services.extraction.time", SimpleNamespace(sleep=no_wait))

    class LostAssignment(SQLiteExtractionRunRepository):
        def assign_job_run(self, extraction_run_id, job_run_id):  # noqa: ANN001
            raise TimeoutError("assignment timed out")

    runs = LostAssignment(database)
    documents = SQLiteDocumentRegistry(database)
    jobs = MockExtractionJobRunner(
        runs, documents, delay_seconds=0.5, parse_runs=SQLiteParseRunRepository(database)
    )
    service = _service(database, runs=runs, jobs=jobs)
    run = asyncio.run(service.start(document_id, "invoice", 1, "ann"))
    assert (run.status, run.job_run_id) == ("RUNNING", None)  # accepted, id not recorded
    claimed = documents.get(document_id)
    assert claimed is not None and claimed.status == "EXTRACTING"  # not rolled back

    # While the job runs, a read finds it by run id and records it.
    healthy = _service(database, jobs=jobs)
    asyncio.run(healthy.list_runs(document_id))
    recorded = SQLiteExtractionRunRepository(database).get(run.extraction_run_id)
    assert recorded is not None and recorded.job_run_id is not None
    for _ in range(100):
        latest = asyncio.run(healthy.list_runs(document_id))[0]
        if latest.status != "RUNNING":
            break
        time.sleep(0.05)
    assert latest.status == "EXTRACTED"
    assert documents.get(document_id).status == "EXTRACTED"  # type: ignore[union-attr]


class UnknownOutcomeRunner:
    """Every trigger times out; whether a job exists is unknown until `find` says."""

    def __init__(self, found: int | None = None) -> None:
        self.found, self.triggers = found, 0

    def trigger(self, requests):  # noqa: ANN001
        self.triggers += 1
        raise TimeoutError("Jobs API timed out")

    def poll(self, job_run_id):  # noqa: ANN001
        raise AssertionError("not reached")

    def find(self, extraction_run_id, since):  # noqa: ANN001
        return self.found


def test_unknown_submission_outcome_keeps_the_claim_until_it_is_established(
    parsed, monkeypatch
) -> None:
    _, _, document_id, database = parsed
    monkeypatch.setattr("idp_app.services.extraction.time", SimpleNamespace(sleep=no_wait))
    jobs = UnknownOutcomeRunner()
    service = _service(database, jobs=jobs)
    run = asyncio.run(service.start(document_id, "invoice", 1, "ann"))
    assert jobs.triggers == 3  # retried with the same idempotency token
    documents = SQLiteDocumentRegistry(database)
    assert run.status == "RUNNING"
    assert documents.get(document_id).status == "EXTRACTING"  # type: ignore[union-attr]

    asyncio.run(service.list_runs(document_id))  # within grace: still undecided
    assert documents.get(document_id).status == "EXTRACTING"  # type: ignore[union-attr]

    runs = SQLiteExtractionRunRepository(database)
    original = runs.get

    def aged(extraction_run_id):  # noqa: ANN001
        found = original(extraction_run_id)
        return found and replace(found, started_at=found.started_at - timedelta(minutes=30))

    runs.get = aged  # type: ignore[method-assign]
    runs_list = runs.list_for_document
    runs.list_for_document = lambda d: [  # type: ignore[method-assign]
        replace(r, started_at=r.started_at - timedelta(minutes=30)) for r in runs_list(d)
    ]
    asyncio.run(_service(database, runs=runs, jobs=jobs).list_runs(document_id))
    failed = original(run.extraction_run_id)
    assert failed is not None and failed.status == "FAILED"
    assert documents.get(document_id).status == "EXTRACT_FAILED"  # type: ignore[union-attr]


def test_a_rejected_submission_fails_at_once(parsed) -> None:
    _, _, document_id, database = parsed

    class Rejected(UnknownOutcomeRunner):
        def trigger(self, requests):  # noqa: ANN001
            raise BadRequest("invalid job parameters")

    service = _service(database, jobs=Rejected())
    with pytest.raises(DocumentServiceError) as error:
        asyncio.run(service.start(document_id, "invoice", 1, "ann"))
    assert error.value.status_code == 502
    assert SQLiteDocumentRegistry(database).get(document_id).status == "EXTRACT_FAILED"  # type: ignore[union-attr]


def test_the_extraction_job_writes_the_document_only_while_its_run_owns_it() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "databricks_etl/src/extract_document.py"
    ).read_text()
    statements = [part.split('"""')[0] for part in source.split("{documents}")[1:]]
    assert len(statements) == 3  # the claim check, then the two outcomes
    for statement in statements:
        assert "AND extraction_run_id = :extraction_run_id" in statement


def test_app_still_serves_after_ownership_column(tmp_path: Path) -> None:
    from idp_app.core.config import Settings

    client = TestClient(create_app(Settings(_env_file=None, local_data_dir=tmp_path)))
    assert client.get("/api/documents").status_code == 200
