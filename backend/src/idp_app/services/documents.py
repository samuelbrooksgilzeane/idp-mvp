import hashlib
import re
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


class DocumentService:
    def __init__(
        self,
        storage: DocumentStorage,
        registry: DocumentRegistry,
        max_upload_bytes: int,
    ) -> None:
        self._storage = storage
        self._registry = registry
        self._max_upload_bytes = max_upload_bytes

    async def upload(
        self,
        upload: UploadFile,
        metadata: UploadMetadata,
        uploaded_by: str,
    ) -> DocumentRecord:
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
        with SpooledTemporaryFile(max_size=min(self._max_upload_bytes, 4 * 1024 * 1024)) as staged:
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
                staged.write(chunk)

            if signature != PDF_SIGNATURE:
                raise DocumentServiceError(
                    "UNSUPPORTED_FILE_TYPE",
                    "The uploaded file does not contain a valid PDF signature.",
                    415,
                )

            content_sha256 = digest.hexdigest()
            duplicate = await run_in_threadpool(self._registry.find_by_hash, content_sha256)
            if duplicate is not None:
                raise _duplicate_error(duplicate)

            document_id = str(uuid5(NAMESPACE_URL, f"idp-document:{content_sha256}"))
            object_name = f"{document_id}.pdf"
            try:
                source_path = await run_in_threadpool(
                    self._storage.store,
                    object_name,
                    cast(BinaryIO, staged),
                )
            except FileExistsError as error:
                duplicate = await run_in_threadpool(
                    self._registry.find_by_hash, content_sha256
                )
                if duplicate is not None:
                    raise _duplicate_error(duplicate) from error
                raise DocumentServiceError(
                    "FILE_STORAGE_FAILED",
                    "The PDF could not be stored without overwriting an existing file.",
                    502,
                ) from error
            except Exception as error:
                raise DocumentServiceError(
                    "FILE_STORAGE_FAILED",
                    "The PDF could not be stored.",
                    502,
                ) from error

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
        return document

    async def list_documents(self) -> list[DocumentRecord]:
        try:
            return await run_in_threadpool(self._registry.list_documents)
        except Exception as error:
            raise DocumentServiceError(
                "REGISTRY_READ_FAILED",
                "Documents could not be loaded from the registry.",
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


def sanitize_pdf_filename(filename: str | None) -> str:
    if not filename:
        raise DocumentServiceError(
            "UNSUPPORTED_FILE_TYPE", "A PDF filename is required.", 415
        )
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
