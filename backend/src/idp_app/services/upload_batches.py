from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from idp_app.services.batch_repository import BatchRepository
from idp_app.services.document_models import DocumentRecord, UploadMetadata
from idp_app.services.documents import DocumentService, DocumentServiceError

TERMINAL_UPLOAD_STATES = {"REGISTERED", "ALREADY_REGISTERED"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class UploadBatchService:
    def __init__(
        self,
        repository: BatchRepository,
        documents: DocumentService,
        max_files: int = 1000,
        max_attempts: int = 5,
        lease_seconds: int = 1800,
    ) -> None:
        self.repository = repository
        self.documents = documents
        self.max_files = max_files
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds

    def create(
        self,
        requester: str,
        client_request_id: str,
        case_id: str | None,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not 1 <= len(files) <= self.max_files:
            raise DocumentServiceError("TOO_MANY_FILES", f"Select 1–{self.max_files} PDFs.", 422)
        if len({item["client_file_id"] for item in files}) != len(files):
            raise DocumentServiceError("DUPLICATE_FILE_ID", "File identities must be unique.", 422)
        digest = hashlib.sha256(json.dumps([case_id, files], sort_keys=True).encode()).hexdigest()
        batch_id = str(uuid5(NAMESPACE_URL, f"idp-upload:{requester}:{client_request_id}"))
        now = now_iso()
        header = {
            "batch_id": batch_id,
            "requester": requester,
            "manifest_hash": digest,
            "case_id": case_id,
            "created_at": now,
            "file_count": len(files),
        }
        items = [
            {
                **file,
                "batch_id": batch_id,
                "ordinal": index,
                "state": "QUEUED",
                "document_id": None,
                "content_sha256": None,
                "attempts": 0,
                "lease_owner": None,
                "lease_expires_at": None,
                "error_code": None,
                "error_message": None,
                "retryable": True,
                "revision": 0,
                "transition_id": "initial",
                "updated_at": now,
            }
            for index, file in enumerate(files)
        ]
        self.repository.create(header, items)
        saved = self.authorize(batch_id, requester)
        if saved["manifest_hash"] != digest:
            raise DocumentServiceError(
                "BATCH_REPLAY_CONFLICT",
                "This request identity belongs to a different file selection.",
                409,
            )
        summary = self.summary(batch_id, requester)
        if sum(summary["counts"].values()) != len(files):
            raise DocumentServiceError(
                "BATCH_INITIALIZING", "Retry saving the file selection.", 503
            )
        return {**summary, "items": self.repository.items(batch_id, -1, self.max_files)}

    def authorize(self, batch_id: str, requester: str) -> dict[str, Any]:
        header = self.repository.header(batch_id)
        if header is None or header["requester"] != requester:
            raise DocumentServiceError("BATCH_NOT_FOUND", "Upload batch not found.", 404)
        return header

    def summary(self, batch_id: str, requester: str) -> dict[str, Any]:
        header = self.authorize(batch_id, requester)
        return {
            "batch_id": batch_id,
            "case_id": header["case_id"],
            "created_at": header["created_at"],
            "file_count": header["file_count"],
            "counts": self.repository.counts(batch_id),
        }

    def page(self, batch_id: str, requester: str, after: int, limit: int) -> dict[str, Any]:
        self.authorize(batch_id, requester)
        rows = self.repository.items(batch_id, after, limit + 1)
        return {
            "items": rows[:limit],
            "next_cursor": str(rows[limit - 1]["ordinal"]) if len(rows) > limit else None,
        }

    def transition(self, item: dict[str, Any], **changes: Any) -> dict[str, Any]:
        updated = {
            **item,
            **changes,
            "revision": item["revision"] + 1,
            "transition_id": str(uuid4()),
            "updated_at": now_iso(),
        }
        if not self.repository.compare_and_set(item, updated):
            raise DocumentServiceError(
                "UPLOAD_BUSY", "This file is already being handled. Retry shortly.", 409
            )
        return updated

    def record_transport_failure(
        self, batch_id: str, client_file_id: str, requester: str, code: str
    ) -> dict[str, Any]:
        self.authorize(batch_id, requester)
        item = self.repository.item(batch_id, client_file_id)
        if item is None:
            raise DocumentServiceError("ITEM_NOT_FOUND", "Upload item not found.", 404)
        # A gateway response cannot cancel a server request that may still be committing.
        if item["state"] != "QUEUED":
            return item
        if code == "HTTP_413":
            message = "The PDF is too large for the server or app gateway."
        elif code in {"HTTP_401", "HTTP_403"}:
            message = "Sign in again before retrying this file."
        else:
            message = "The upload connection failed. Retry this file."
        return self.transition(
            item,
            state="FAILED",
            error_code=code,
            error_message=message,
            retryable=code not in {"HTTP_413", "HTTP_401", "HTTP_403"},
        )

    async def upload(
        self, batch_id: str, client_file_id: str, requester: str, upload: UploadFile
    ) -> DocumentRecord:
        header = await run_in_threadpool(self.authorize, batch_id, requester)
        found = await run_in_threadpool(self.repository.item, batch_id, client_file_id)
        if found is None:
            raise DocumentServiceError(
                "ITEM_NOT_FOUND", "This file is not in the upload batch.", 404
            )
        item: dict[str, Any] = found
        if item["state"] in TERMINAL_UPLOAD_STATES:
            return await self.documents.get_document(item["document_id"])
        if item["content_sha256"]:
            registered = await self.documents.find_registered_content(item["content_sha256"])
            if registered is not None:
                await run_in_threadpool(
                    self.transition,
                    item,
                    state="ALREADY_REGISTERED",
                    document_id=registered.document_id,
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=None,
                    error_message=None,
                    retryable=False,
                )
                return registered
        if upload.filename != item["name"] or upload.size != item["size"]:
            raise DocumentServiceError(
                "FILE_MANIFEST_MISMATCH",
                "Reselect the original file with the same name and size.",
                422,
            )
        if item["state"] == "UPLOADING" and item["lease_expires_at"] > now_iso():
            raise DocumentServiceError(
                "UPLOAD_BUSY", "This file is already uploading. Retry after it finishes.", 409
            )
        if item["attempts"] >= self.max_attempts:
            raise DocumentServiceError(
                "UPLOAD_ATTEMPTS_EXHAUSTED",
                "Upload retry limit reached. Start a new batch after resolving the failure.",
                409,
            )
        item = await run_in_threadpool(
            self.transition,
            item,
            state="UPLOADING",
            attempts=item["attempts"] + 1,
            lease_owner=str(uuid4()),
            lease_expires_at=(
                datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
            ).isoformat(),
            error_code=None,
            error_message=None,
        )

        async def bind_content(content_hash: str) -> None:
            nonlocal item
            if item["content_sha256"] and item["content_sha256"] != content_hash:
                raise DocumentServiceError(
                    "FILE_CONTENT_MISMATCH",
                    "The reselected PDF has different content. Start a new batch for it.",
                    409,
                )
            if item["lease_expires_at"] <= now_iso():
                raise DocumentServiceError(
                    "UPLOAD_LEASE_EXPIRED", "The transfer took too long. Retry this file.", 503
                )
            item = await run_in_threadpool(self.transition, item, content_sha256=content_hash)

        try:
            document = await self.documents.upload(
                upload,
                UploadMetadata(
                    case_id=header["case_id"], template_id="invoice_v1", use_case="invoice"
                ),
                requester,
                on_content=bind_content,
            )
            state = "REGISTERED"
        except DocumentServiceError as error:
            if error.code == "DOCUMENT_DUPLICATE" and error.document_id:
                document = await self.documents.get_document(error.document_id)
                state = "ALREADY_REGISTERED"
            else:
                await run_in_threadpool(
                    self.transition,
                    item,
                    state="FAILED",
                    lease_owner=None,
                    lease_expires_at=None,
                    error_code=error.code,
                    error_message=error.message,
                    retryable=error.status_code >= 500 or error.status_code == 429,
                )
                raise
        except Exception as error:
            await run_in_threadpool(
                self.transition,
                item,
                state="FAILED",
                lease_owner=None,
                lease_expires_at=None,
                error_code="UPLOAD_INTERRUPTED",
                error_message="Upload interrupted. Retry this file.",
                retryable=True,
            )
            raise DocumentServiceError(
                "UPLOAD_INTERRUPTED", "Upload interrupted. Retry this file.", 502
            ) from error
        await run_in_threadpool(
            self.transition,
            item,
            state=state,
            document_id=document.document_id,
            lease_owner=None,
            lease_expires_at=None,
            retryable=False,
        )
        return document
