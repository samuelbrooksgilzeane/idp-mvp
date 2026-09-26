"""Extraction adapter for the shared serialized dispatcher and retained-result recovery."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.extraction import EXTRACTOR_VERSION, extraction_idempotency_key
from idp_app.services.extraction_batches import ExtractionBatchService
from idp_app.services.extraction_inputs import verify_schema_content
from idp_app.services.extraction_result import (
    build_invoice_candidates,
    build_invoice_line_candidates,
    flatten_result,
    walk_extraction,
)
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.work_batches import WorkItem, WorkRepository, changed, now_iso


class ExtractionQueue:
    kind = "EXTRACT"

    def __init__(
        self, batches: ExtractionBatchService, work: WorkRepository, runs: ExtractionRunRepository
    ) -> None:
        self.batches, self.work, self.runs = batches, work, runs

    def reconcile_missing_intents(self, max_documents: int = 100) -> int:
        pending = self.batches.batches.pending(1)
        if pending:
            self.batches.resolve_step(pending[0].batch_id, max_documents)
        ready = self.batches.batches.pending(1, "READY")
        if ready:
            batch = ready[0]
            members = batch.resolved[batch.published : batch.published + max_documents]
            for member in members:
                self.work.put_item(
                    WorkItem(
                        work_item_id=member["work_item_id"],
                        document_id=member["document_id"],
                        content_sha256=member.get("content_sha256", ""),
                        parser_version="",
                        parse_run_id=member.get("parse_run_id", ""),
                        requested_by=batch.requested_by,
                        kind=self.kind,
                        batch_id=batch.batch_id,
                        ordinal=member["ordinal"],
                        extraction_run_id=member["extraction_run_id"],
                        schema_id=batch.schema_id,
                        schema_version=batch.schema_version,
                        schema_hash=batch.schema_hash,
                        state=member["state"],
                        error_code=member.get("error_code"),
                        error_message=member.get("error_message"),
                        next_eligible_at=batch.created_at,
                        created_at=batch.created_at,
                        updated_at=now_iso(),
                    )
                )
            count = batch.published + len(members)
            self.batches.batches.update(
                batch,
                replace(
                    batch,
                    published=count,
                    state="ENQUEUED" if count == len(batch.resolved) else "READY",
                    revision=batch.revision + 1,
                    transition_id=str(uuid4()),
                ),
            )
        return int(bool(pending or ready))

    def ensure_run(self, item: WorkItem) -> ExtractionRunRecord:
        if (
            not item.extraction_run_id
            or not item.schema_id
            or not item.schema_version
            or not item.schema_hash
        ):
            raise ValueError("Extraction work has no pinned inputs")
        current = self.runs.get(item.extraction_run_id)
        if current is not None:
            if (
                current.document_id != item.document_id
                or current.parse_run_id != item.parse_run_id
                or current.schema_hash != item.schema_hash
                or current.schema_id != item.schema_id
                or current.schema_version != item.schema_version
                or current.requested_by != item.requested_by
            ):
                raise ValueError("Extraction run identity conflicts with work inputs")
            return current
        run = ExtractionRunRecord(
            item.extraction_run_id,
            item.document_id,
            item.parse_run_id,
            item.schema_id,
            item.schema_version,
            item.schema_hash,
            EXTRACTOR_VERSION,
            {
                "work_item_id": item.work_item_id,
                "version": EXTRACTOR_VERSION,
                "mode": "precision",
                "enableCitations": "true",
                "enableConfidenceScores": "true",
                "idempotency_key": extraction_idempotency_key(
                    item.document_id,
                    item.parse_run_id,
                    item.schema_id,
                    item.schema_version,
                    EXTRACTOR_VERSION,
                ),
            },
            None,
            None,
            "RUNNING",
            item.requested_by,
            None,
            datetime.now(UTC),
            None,
        )
        self.runs.create(run)
        saved = self.runs.get(run.extraction_run_id)
        assert saved is not None
        return saved

    def assign_job(self, item: WorkItem, job_run_id: int) -> None:
        assert item.extraction_run_id
        self.runs.assign_job_run(item.extraction_run_id, job_run_id)

    def project(self, item: WorkItem) -> None:
        """Rebuild from committed inference only, with no AI invocation."""
        run = self.ensure_run(item)
        if run.status == "EXTRACTED":
            return
        if run.ai_result is None:
            raise ValueError("No retained result to project")
        if run.ai_result.get("error_message"):
            self.runs.fail(run.extraction_run_id, str(run.ai_result["error_message"])[:500])
            return
        schema = self.batches.schemas.get(run.schema_id, run.schema_version)
        document = self.batches.documents.get(run.document_id)
        if schema is None or schema.schema_hash != run.schema_hash or document is None:
            raise ValueError("Pinned projection inputs are unavailable")
        verify_schema_content(schema)
        fields = flatten_result(run, schema, run.ai_result)
        records, generic = walk_extraction(run, schema, run.ai_result)
        self.runs.complete(
            run.extraction_run_id,
            fields,
            build_invoice_candidates(run, document, fields),
            build_invoice_line_candidates(run, fields),
            records,
            generic,
        )

    def settle(self, item: WorkItem, max_attempts: int) -> None:
        if item.state == "QUEUED":
            return
        run = self.ensure_run(item)
        if run.status == "RUNNING" and run.ai_result is not None:
            try:
                self.project(item)
            except ValueError as error:
                self.runs.fail(run.extraction_run_id, str(error)[:500])
            run = self.ensure_run(item)
        if run.status == "EXTRACTED":
            if item.state != "SUCCEEDED":
                self.work.update_item(
                    item,
                    changed(
                        item,
                        state="SUCCEEDED",
                        lease_expires_at=None,
                        error_code=None,
                        error_message=None,
                    ),
                )
            self.mark_document(item, "EXTRACTED")
            return
        transient = run.status == "RUNNING" or item.error_code in {
            "TRANSIENT_EXTRACTION_ERROR",
            "DOCUMENT_BUSY",
        }
        if run.status == "RUNNING":
            self.runs.fail(run.extraction_run_id, "The Job ended without committing a result.")
        retry = transient and item.attempts < max_attempts
        updated = changed(
            item,
            state="QUEUED" if retry else "FAILED",
            extraction_run_id=str(uuid4()) if retry else item.extraction_run_id,
            dispatch_id=None if retry else item.dispatch_id,
            execution_owner=None,
            lease_expires_at=None,
            next_eligible_at=(
                datetime.now(UTC) + timedelta(seconds=30 * 2 ** max(0, item.attempts - 1))
            ).isoformat(),
            error_code="RETRY_PENDING" if retry else (item.error_code or "EXTRACTION_FAILED"),
            error_message=run.error_message or "Extraction failed.",
        )
        if self.work.update_item(item, updated) and item.error_code != "DOCUMENT_BUSY":
            self.mark_document(item, "EXTRACT_FAILED")

    def mark_document(self, item: WorkItem, status: str) -> None:
        # The work/run owns the outcome. The overview is only a best-effort projection.
        document = self.batches.documents.get(item.document_id)
        if item.error_code == "DOCUMENT_BUSY":
            return
        latest = self.runs.list_for_document(item.document_id)
        if latest and latest[0].extraction_run_id != item.extraction_run_id:
            return
        if document and document.status == "EXTRACTING":
            from idp_app.services.document_registry import InvalidDocumentStateError

            with suppress(InvalidDocumentStateError):
                self.batches.documents.update_status(item.document_id, {"EXTRACTING"}, status)
