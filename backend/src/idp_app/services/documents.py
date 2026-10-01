from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, cast
from uuid import NAMESPACE_URL, uuid5

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import DocumentRecord, UploadMetadata
from idp_app.services.document_registry import (
    DocumentRegistry,
    DuplicateDocumentError,
)
from idp_app.services.document_storage import DocumentStorage

PDF_SIGNATURE = b"%PDF-"
SAFE_FILE_CHARACTER = re.compile(r"[^A-Za-z0-9._-]+")


class DocumentServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int,
        *,
        document_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.document_id = document_id


@dataclass
class StagedUpload:
    """A fully received, validated PDF body waiting to be stored."""

    file_name: str
    size: int
    content_sha256: str
    file: SpooledTemporaryFile[bytes]

    def close(self) -> None:
        self.file.close()

    def __enter__(self) -> StagedUpload:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class DocumentService:
    def __init__(
        self,
        storage: DocumentStorage,
        registry: DocumentRegistry,
        max_upload_bytes: int,
        on_registered: Callable[[DocumentRecord], object] | None = None,
        on_source_changed: Callable[[], object] | None = None,
    ) -> None:
        self._storage = storage
        self._registry = registry
        self._max_upload_bytes = max_upload_bytes
        self._on_registered = on_registered
        # Non-blocking notice that the source volume gained or lost a PDF (KA Sync).
        self._on_source_changed = on_source_changed

    async def upload(
        self,
        upload: UploadFile,
        metadata: UploadMetadata,
        uploaded_by: str,
    ) -> DocumentRecord:
        with await self.stage(upload) as staged:
            return await self.store_and_register(staged, metadata, uploaded_by)

    async def stage(self, upload: UploadFile) -> StagedUpload:
        """Read the whole body into a spooled file, checking type, size and PDF signature.

        No storage or registry work happens here, so callers can record the content hash in the
        same write that claims the upload. Close the result (it is a context manager)."""
        safe_name = sanitize_pdf_filename(upload.filename)
        if upload.content_type != "application/pdf":
            raise DocumentServiceError(
                "UNSUPPORTED_FILE_TYPE",
                "Only PDF files with the application/pdf media type are accepted.",
                415,
            )

        digest = hashlib.sha256()
        size = 0
        signature = b""
        # Closed on failure below, otherwise by the caller through StagedUpload.
        spooled = SpooledTemporaryFile(  # noqa: SIM115
            max_size=min(self._max_upload_bytes, 4 * 1024 * 1024)
        )
        try:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > self._max_upload_bytes:
                    raise DocumentServiceError(
                        "FILE_TOO_LARGE",
                        f"PDF exceeds the {self._max_upload_bytes}-byte upload limit.",
                        413,
                    )
                if len(signature) < len(PDF_SIGNATURE):
                    signature += chunk[: len(PDF_SIGNATURE) - len(signature)]
                digest.update(chunk)
                spooled.write(chunk)

            if signature != PDF_SIGNATURE:
                raise DocumentServiceError(
                    "UNSUPPORTED_FILE_TYPE",
                    "The uploaded file does not contain a valid PDF signature.",
                    415,
                )
        except BaseException:
            spooled.close()
            raise
        spooled.seek(0)
        return StagedUpload(safe_name, size, digest.hexdigest(), spooled)

    async def store_and_register(
        self,
        staged: StagedUpload,
        metadata: UploadMetadata,
        uploaded_by: str,
        *,
        register_by: datetime | None = None,
    ) -> DocumentRecord:
        """Store a staged PDF in the source volume and add its registry row.

        ``register_by`` is a local deadline checked just before the registry write, so an upload
        whose claim has lapsed while storing does not register behind another request."""
        safe_name = staged.file_name
        size = staged.size
        content_sha256 = staged.content_sha256
        duplicate = await run_in_threadpool(self._registry.find_by_hash, content_sha256)
        if duplicate is not None:
            raise _duplicate_error(duplicate)

        document_id = str(uuid5(NAMESPACE_URL, f"idp-document:{content_sha256}"))
        object_name = f"{document_id}.pdf"
        try:
            source_path = await run_in_threadpool(
                self._storage.store,
                object_name,
                cast(BinaryIO, staged.file),
            )
        except FileExistsError as error:
            duplicate = await run_in_threadpool(self._registry.find_by_hash, content_sha256)
            if duplicate is not None:
                raise _duplicate_error(duplicate) from error
            # Recover only a byte-for-byte verified object; ambiguous or partial files stay put.
            try:
                source_path = await run_in_threadpool(
                    self._storage.verify_existing,
                    object_name,
                    content_sha256,
                    size,
                )
            except Exception as recovery_error:
                raise DocumentServiceError(
                    "FILE_STORAGE_FAILED",
                    "An existing source PDF could not be verified. It needs reconciliation.",
                    502,
                ) from recovery_error
        except Exception as error:
            raise DocumentServiceError(
                "FILE_STORAGE_FAILED",
                "The PDF could not be stored.",
                502,
            ) from error

        if register_by is not None and datetime.now(UTC) >= register_by:
            raise DocumentServiceError(
                "UPLOAD_LEASE_EXPIRED", "The transfer took too long. Retry this file.", 503
            )
        now = datetime.now(UTC)
        document = DocumentRecord(
            document_id=document_id,
            case_id=metadata.case_id,
            template_id=metadata.template_id,
            use_case=metadata.use_case,
            source_path=source_path,
            file_name=safe_name,
            file_size=size,
            content_sha256=content_sha256,
            selected_schema_id=None,
            selected_schema_version=None,
            status="UPLOADED",
            uploaded_by=uploaded_by,
            uploaded_at=now,
            updated_at=now,
        )
        try:
            await run_in_threadpool(self._registry.add, document)
        except DuplicateDocumentError as error:
            raise _duplicate_error(error.document) from error
        except Exception as error:
            raise DocumentServiceError(
                "REGISTRY_WRITE_FAILED",
                "The PDF was stored, but its registry record could not be committed.",
                502,
            ) from error
        self._source_changed()
        if self._on_registered is not None:
            try:
                await run_in_threadpool(self._on_registered, document)
                refreshed = await run_in_threadpool(self._registry.get, document.document_id)
                if refreshed is not None:
                    document = refreshed
            except Exception:
                # Registration is already durable. The dispatcher reconciles UPLOADED gaps.
                logging.getLogger(__name__).warning(
                    "Parse intent needs registration reconciliation"
                )
        return document

    def reconcile_sources(self, max_objects: int = 10000) -> list[dict[str, str]]:
        """Read-only snapshot audit. In-flight uploads can appear as ambiguous orphans."""
        from idp_app.services.source_reconciliation import reconcile_sources

        return reconcile_sources(self._registry, self._storage.list_source_paths(), max_objects)

    async def find_registered_content(self, content_hash: str) -> DocumentRecord | None:
        return await run_in_threadpool(self._registry.find_by_hash, content_hash)

    async def list_documents(self, case_id: str | None = None) -> list[DocumentRecord]:
        try:
            if case_id is None:
                return await run_in_threadpool(self._registry.list_documents)
            return await run_in_threadpool(self._registry.list_documents, case_id)
        except Exception as error:
            raise DocumentServiceError(
                "REGISTRY_READ_FAILED",
                "Documents could not be loaded from the registry.",
                502,
            ) from error

    async def list_document_page(
        self,
        case_id: str | None,
        status: str | None,
        search: str,
        cursor: str | None,
        limit: int,
    ) -> tuple[list[DocumentRecord], str | None]:
        from idp_app.services.document_registry import document_page_cursor, document_page_query

        try:
            document_page_query(case_id, status, search, cursor)
        except ValueError as error:
            raise DocumentServiceError("INVALID_CURSOR", str(error), 422) from error
        try:
            rows = await run_in_threadpool(
                self._registry.list_document_page,
                case_id,
                status,
                search,
                cursor,
                limit,
            )
        except Exception as error:
            raise DocumentServiceError(
                "REGISTRY_READ_FAILED",
                "Documents could not be loaded from the registry.",
                502,
            ) from error
        items = rows[:limit]
        next_cursor = (
            document_page_cursor(items[-1], case_id, status, search) if len(rows) > limit else None
        )
        return items, next_cursor

    async def list_case_ids(self) -> list[str]:
        try:
            return await run_in_threadpool(self._registry.list_case_ids)
        except Exception as error:
            raise DocumentServiceError(
                "REGISTRY_READ_FAILED",
                "Document cases could not be loaded from the registry.",
                502,
            ) from error

    async def get_document(self, document_id: str) -> DocumentRecord:
        try:
            document = await run_in_threadpool(self._registry.get, document_id)
        except Exception as error:
            raise DocumentServiceError(
                "REGISTRY_READ_FAILED",
                "The document could not be loaded from the registry.",
                502,
            ) from error
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        return document

    async def delete_document(self, document_id: str) -> None:
        document = await self.get_document(document_id)
        if document.status == "DELETED":
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        object_name = f"{document.document_id}.pdf"
        try:
            await run_in_threadpool(self._storage.delete, object_name)
        except Exception as error:
            raise DocumentServiceError(
                "FILE_DELETE_FAILED", "The uploaded PDF could not be deleted.", 502
            ) from error
        try:
            await run_in_threadpool(self._registry.delete, document_id)
        except KeyError as error:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404) from error
        except Exception as error:
            raise DocumentServiceError(
                "REGISTRY_WRITE_FAILED", "The document could not be removed from the registry.", 502
            ) from error
        finally:
            self._source_changed()  # the PDF is already gone from the volume

    def _source_changed(self) -> None:
        if self._on_source_changed is None:
            return
        try:
            self._on_source_changed()
        except Exception:
            logging.getLogger(__name__).warning("Source change notification failed")


def sanitize_pdf_filename(filename: str | None) -> str:
    if not filename:
        raise DocumentServiceError("UNSUPPORTED_FILE_TYPE", "A PDF filename is required.", 415)
    normalized = filename.replace("\\", "/")
    basename = PurePosixPath(normalized).name
    sanitized = SAFE_FILE_CHARACTER.sub("_", basename).lstrip(".")
    if not sanitized or PurePosixPath(sanitized).suffix.lower() != ".pdf":
        raise DocumentServiceError(
            "UNSUPPORTED_FILE_TYPE", "Only files with a .pdf extension are accepted.", 415
        )
    return f"{sanitized[:-4][:251]}.pdf"


def _duplicate_error(document: DocumentRecord) -> DocumentServiceError:
    return DocumentServiceError(
        "DOCUMENT_DUPLICATE",
        f"This PDF is already registered as {document.file_name}.",
        409,
        document_id=document.document_id,
    )
