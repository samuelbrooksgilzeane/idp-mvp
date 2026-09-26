from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from idp_app.core.config import IdpMode, Settings
from idp_app.services.document_models import DocumentRecord, ParseRunRecord
from idp_app.services.document_registry import (
    DocumentRegistry,
    InvalidDocumentStateError,
    document_page_cursor,
)
from idp_app.services.documents import DocumentServiceError
from idp_app.services.parse_runs import ParseRunRepository
from idp_app.services.work_batches import WorkItem, WorkRepository, changed, now_iso

PARSER_VERSION = "2.0"
ACTIVE = {"QUEUED", "CLAIMED", "RUNNING"}


def initial_work_id(document: DocumentRecord) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"initial-parse:{document.document_id}:{document.content_sha256}:{PARSER_VERSION}",
        )
    )


class PreparationService:
    def __init__(
        self,
        settings: Settings,
        documents: DocumentRegistry,
        runs: ParseRunRepository,
        work: WorkRepository,
        wake: Callable[[], None] | None = None,
    ) -> None:
        self.settings, self.documents, self.runs, self.work = settings, documents, runs, work
        self.wake = wake

    def request(
        self, document_id: str, requester: str, *, retry: bool = False, reparse: bool = False
    ) -> ParseRunRecord:
        document = self.documents.get(document_id)
        if document is None or document.status == "DELETED":
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        identity = initial_work_id(document)
        existing = self.work.item(identity)
        if existing and existing.state in ACTIVE:
            run = self.ensure_run(existing)
            if document.status == "UPLOADED":
                self.mark_document(existing, "PARSE_QUEUED")
            return run
        successful = self.runs.latest_successful(document_id)
        if (
            successful
            and successful.content_sha256 == document.content_sha256
            and successful.parser_version == PARSER_VERSION
            and not reparse
        ):
            if document.status == "UPLOADED":
                with suppress(InvalidDocumentStateError):
                    self.documents.update_status(document_id, {"UPLOADED"}, "PARSED")
            return successful
        if document.status in {"PARSING", "EXTRACTING", "VALIDATING"}:
            raise DocumentServiceError(
                "DOCUMENT_BUSY", "The document is already being processed.", 409
            )
        now = now_iso()
        if existing:
            if not (retry or reparse):
                if existing.state == "FAILED":
                    self.mark_document(existing, "PARSE_FAILED")
                return self.ensure_run(existing)
            item = changed(
                existing,
                state="QUEUED",
                parse_run_id=str(uuid4()),
                attempts=0,
                requested_by=requester,
                next_eligible_at=now,
                dispatch_id=None,
                lease_expires_at=None,
                error_code=None,
                error_message=None,
            )
            if not self.work.update_item(existing, item):
                raise DocumentServiceError(
                    "PREPARATION_BUSY", "Preparation changed; refresh and retry.", 409
                )
        else:
            item = self.work.put_item(
                WorkItem(
                    identity,
                    document_id,
                    document.content_sha256,
                    PARSER_VERSION,
                    str(uuid5(NAMESPACE_URL, f"parse-run:{identity}:initial")),
                    requester,
                    next_eligible_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        run = self.ensure_run(item)
        self.mark_document(item, "PARSE_QUEUED")
        if self.wake:
            self.wake()
        return run

    def ensure_run(self, item: WorkItem) -> ParseRunRecord:
        run = self.runs.get(item.parse_run_id)
        if run:
            return run
        if self.settings.mode is IdpMode.MOCK:
            root = (
                self.settings.local_data_dir
                / "artifacts_volume"
                / "page_images"
                / item.document_id
                / item.parse_run_id
            ).as_posix()
        else:
            root = (
                f"/Volumes/{self.settings.catalog}/{self.settings.project_schema}/"
                f"{self.settings.artifacts_volume_name}/page_images/{item.document_id}/{item.parse_run_id}"
            )
        self.runs.create(
            ParseRunRecord(
                parse_run_id=item.parse_run_id,
                document_id=item.document_id,
                content_sha256=item.content_sha256,
                parser_version=item.parser_version,
                parsed=None,
                document_text=None,
                page_count=None,
                page_image_root=root,
                parse_error=None,
                status="QUEUED",
                requested_by=item.requested_by,
                job_run_id=None,
                started_at=datetime.fromisoformat(item.updated_at),
                completed_at=None,
            )
        )
        created = self.runs.get(item.parse_run_id)
        if created is None:
            raise RuntimeError("Queued parse run could not be read")
        return created

    def mark_document(self, item: WorkItem, status: str) -> None:
        document = self.documents.get(item.document_id)
        if document is None or document.status == "DELETED":
            return
        allowed = {
            "PARSED": {"PARSING", "PARSE_QUEUED", "UPLOADED"},
            "PARSE_FAILED": {"PARSING", "PARSE_QUEUED", "UPLOADED"},
            "PARSING": {"PARSE_QUEUED", "UPLOADED", "PARSE_FAILED"},
            "PARSE_QUEUED": {
                "UPLOADED",
                "PARSE_FAILED",
                "PARSED",
                "EXTRACTED",
                "EXTRACT_FAILED",
                "VALIDATED_PASS",
                "REVIEW_REQUIRED",
            },
        }
        if document.status not in allowed.get(status, set()):
            return
        # Another workflow may own the overview; immutable runs remain authoritative.
        with suppress(InvalidDocumentStateError):
            self.documents.update_status(item.document_id, {document.status}, status)

    def reconcile_missing_intents(self, max_documents: int = 1000) -> int:
        """Bounded metadata scan repairs registration/intent gaps, never runs inference."""
        queued = 0
        cursor = None
        scanned = 0
        while scanned < max_documents:
            rows = self.documents.list_document_page(None, "UPLOADED", "", cursor, 100)
            for document in rows[: min(100, max_documents - scanned)]:
                self.request(document.document_id, document.uploaded_by)
                queued += 1
                scanned += 1
            if len(rows) <= 100:
                break
            cursor = document_page_cursor(rows[99], None, "UPLOADED", "")
        return queued
