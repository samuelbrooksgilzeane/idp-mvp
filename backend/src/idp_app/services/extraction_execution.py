"""Claimed extraction execution, shared by mock and Databricks manifest tasks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from idp_app.services.document_models import DocumentRecord, ParseRunRecord
from idp_app.services.document_registry import InvalidDocumentStateError
from idp_app.services.extraction_queue import ExtractionQueue
from idp_app.services.schema_models import SchemaRecord
from idp_app.services.work_batches import WorkItem, changed, now_iso


def check_owner(queue: ExtractionQueue, item: WorkItem) -> None:
    current = queue.work.item(item.work_item_id)
    if (
        current is None
        or current.state != "RUNNING"
        or current.execution_owner != item.execution_owner
        or current.dispatch_id != item.dispatch_id
        or current.extraction_run_id != item.extraction_run_id
    ):
        raise ValueError("Extraction task ownership changed")


def execute_extraction(
    queue: ExtractionQueue,
    dispatch_id: str,
    work_item_id: str,
    infer: Callable[[DocumentRecord, ParseRunRecord, SchemaRecord], dict[str, Any]],
) -> None:
    dispatch = queue.work.dispatch(dispatch_id)
    item = queue.work.item(work_item_id)
    if (
        dispatch is None
        or dispatch.kind != "EXTRACT"
        or dispatch.state not in {"SUBMITTING", "ASSIGNING", "RUNNING"}
        or work_item_id not in dispatch.work_item_ids
        or item is None
        or item.kind != "EXTRACT"
        or item.dispatch_id != dispatch_id
    ):
        raise ValueError("Task does not belong to an active extraction manifest")
    run = queue.ensure_run(item)
    if run.status == "EXTRACTED":
        return
    if item.state != "CLAIMED" or not item.lease_expires_at or item.lease_expires_at <= now_iso():
        raise ValueError("Extraction task needs a valid unconsumed claim")
    claimed = changed(item, state="RUNNING", execution_owner=str(uuid4()))
    if not queue.work.update_item(item, claimed):
        raise ValueError("Another extraction task consumed the claim")
    item = claimed
    retained = run.ai_result is not None
    document_owned = False
    try:
        document = queue.batches.documents.get(item.document_id)
        parse = queue.batches.parses.get(item.parse_run_id)
        schema = queue.batches.schemas.get(run.schema_id, run.schema_version)
        if (
            document is None
            or document.status == "DELETED"
            or document.content_sha256 != item.content_sha256
            or parse is None
            or parse.status != "SUCCESS"
            or parse.document_id != item.document_id
            or parse.content_sha256 != item.content_sha256
            or schema is None
            or schema.schema_hash != item.schema_hash
            or schema.status not in {"PRODUCTION", "PUBLISHED"}
        ):
            raise ValueError("Pinned extraction inputs changed or are unavailable")
        from idp_app.services.extraction_inputs import (
            ELIGIBLE_DOCUMENT_STATES,
            verify_schema_content,
        )

        verify_schema_content(schema)
        queue.batches.documents.begin_extraction(
            item.document_id,
            ELIGIBLE_DOCUMENT_STATES,
            run.schema_id,
            run.schema_version,
            run.extraction_run_id,
        )
        document_owned = True
        if not retained:
            check_owner(queue, item)
            result = infer(document, parse, schema)
            check_owner(queue, item)
            queue.runs.retain_raw(run.extraction_run_id, result)
            retained = True
        check_owner(queue, item)
        queue.project(item)
        saved = queue.ensure_run(item)
        check_owner(queue, item)
        current = queue.work.item(item.work_item_id)
        assert current is not None
        if not queue.work.update_item(
            current,
            changed(
                current,
                state="SUCCEEDED" if saved.status == "EXTRACTED" else "RUNNING",
                error_code=None if saved.status == "EXTRACTED" else "EXTRACTION_FAILED",
                error_message=saved.error_message,
                lease_expires_at=None,
            ),
        ):
            raise ValueError("Extraction terminal outcome changed")
        queue.mark_document(item, "EXTRACTED" if saved.status == "EXTRACTED" else "EXTRACT_FAILED")
    except Exception as error:
        # Ambiguous persistence can have succeeded. Reconcile the retained row centrally.
        saved = queue.ensure_run(item)
        if retained or saved.ai_result is not None or saved.status == "EXTRACTED":
            raise
        check_owner(queue, item)
        busy = isinstance(error, InvalidDocumentStateError)
        transient = busy or any(
            code in str(error).upper()
            for code in ("TIMEOUT", "TEMPORARILY_UNAVAILABLE", "RESOURCE_EXHAUSTED", "429", "503")
        )
        queue.runs.fail(run.extraction_run_id, str(error)[:500])
        current = queue.work.item(item.work_item_id)
        assert current is not None
        queue.work.update_item(
            current,
            changed(
                current,
                state="RUNNING",
                lease_expires_at=None,
                error_code="DOCUMENT_BUSY"
                if busy
                else ("TRANSIENT_EXTRACTION_ERROR" if transient else "EXTRACTION_FAILED"),
                error_message=str(error)[:500],
            ),
        )
        if document_owned:
            queue.mark_document(item, "EXTRACT_FAILED")
        raise
