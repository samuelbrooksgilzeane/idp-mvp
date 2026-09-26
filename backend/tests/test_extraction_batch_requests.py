"""Durable request recovery tests using local metadata only."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from test_bulk_extraction_inputs import fixture

from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_batches import (
    ExtractionBatchService,
    SQLiteExtractionBatchRepository,
)
from idp_app.services.schema_registry import SQLiteSchemaRepository


def service(tmp_path, count=3):
    documents, parses, schema = fixture(tmp_path, count)
    batches = SQLiteExtractionBatchRepository(tmp_path / "metadata.sqlite3")
    return ExtractionBatchService(
        batches,
        documents,
        parses,
        SQLiteSchemaRepository(tmp_path / "metadata.sqlite3"),
        "project-one",
    ), schema


def create(service, schema, ids=None):
    return service.create(
        ids or ["0", "1", "2"], schema.schema_id, schema.schema_version, "owner", "request-one"
    )


def test_submission_is_durable_without_document_reads_and_replays(tmp_path):
    queue, schema = service(tmp_path)
    with (
        patch.object(queue.documents, "get_many") as documents,
        patch.object(queue.parses, "latest_successful_references") as parses,
    ):
        batch = create(queue, schema)
        assert batch.state == "VALIDATING" and not batch.resolved
        assert create(queue, schema) == batch
        documents.assert_not_called()
        parses.assert_not_called()
    restarted = ExtractionBatchService(
        SQLiteExtractionBatchRepository(tmp_path / "metadata.sqlite3"),
        queue.documents,
        queue.parses,
        queue.schemas,
        queue.project,
    )
    assert restarted.owned(batch.batch_id, "owner") == batch
    with pytest.raises(DocumentServiceError) as error:
        restarted.owned(batch.batch_id, "other")
    assert error.value.status_code == 404
    with pytest.raises(DocumentServiceError) as error:
        create(queue, schema, ["1"])
    assert error.value.code == "BATCH_REQUEST_CONFLICT"


def test_resolution_checkpoints_pin_inputs_across_restart_and_reparse(tmp_path):
    queue, schema = service(tmp_path)
    batch = create(queue, schema)
    partial = queue.resolve_step(batch.batch_id, 1)
    assert partial.state == "VALIDATING" and len(partial.resolved) == 1
    first = partial.resolved[0]
    old = queue.parses.get(first["parse_run_id"])
    queue.parses.create(replace(old, parse_run_id="newer", completed_at=datetime.now(UTC)))
    restarted = ExtractionBatchService(
        SQLiteExtractionBatchRepository(tmp_path / "metadata.sqlite3"),
        queue.documents,
        queue.parses,
        queue.schemas,
        queue.project,
    )
    done = restarted.resolve_step(batch.batch_id)
    assert done.state == "READY"
    assert done.resolved[0] == first
    assert restarted.resolve_step(batch.batch_id) == done
    assert len({member["extraction_run_id"] for member in done.resolved}) == 3


def test_schema_change_is_a_persisted_per_member_failure(tmp_path):
    queue, schema = service(tmp_path)
    batch = create(queue, schema)
    with patch.object(queue.schemas, "get", return_value=replace(schema, schema_hash="changed")):
        done = queue.resolve_step(batch.batch_id)
    assert done.state == "READY"
    assert {member["error_code"] for member in done.resolved} == {"SCHEMA_CHANGED"}


def test_concurrent_resolution_cannot_overwrite_winning_pins(tmp_path):
    queue, schema = service(tmp_path)
    batch = create(queue, schema)
    original_update = queue.batches.update

    def competing_update(old, new):
        winner = replace(new, transition_id="winner")
        assert original_update(old, winner)
        assert not original_update(old, new)
        return False

    with patch.object(queue.batches, "update", side_effect=competing_update):
        result = queue.resolve_step(batch.batch_id, 1)
    assert result.transition_id == "winner"
    assert len(result.resolved) == 1


def test_thousand_member_request_recovers_in_ten_small_steps(tmp_path):
    queue, schema = service(tmp_path, 1000)
    batch = create(queue, schema, [str(index) for index in range(1000)])
    for step in range(10):
        result = queue.resolve_step(batch.batch_id)
        assert len(result.resolved) == (step + 1) * 100
    assert result.state == "READY"
    assert not queue.batches.pending(1)
