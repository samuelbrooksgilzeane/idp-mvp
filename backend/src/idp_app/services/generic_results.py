"""Generic (schema-agnostic) extraction results: section 6 of the generalized IDP plan.

Review reads are deliberately read-only. New extraction jobs retain the recursive record tree;
older runs without that projection are reconstructed in memory from the immutable ai_extract
result. A page view must never issue a row-by-row persistence workload.

The Results list has its own compact repository read model. It neither reads raw `ai_result` nor
touches the generic cache, so it stays a single paginated query rather than paying detail-view
cost for every run on the page.
"""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import (
    DocumentRecord,
    ExtractedRecordRow,
    ExtractionRunListRecord,
    ExtractionRunRecord,
    GenericFieldRow,
)
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_result import walk_extraction
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.schema_models import SchemaRecord
from idp_app.services.schema_registry import SchemaRepository

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenericExtractionResult:
    run: ExtractionRunRecord
    schema: SchemaRecord
    hierarchy: dict[str, Any]


@dataclass(frozen=True)
class GenericExtractionRecords:
    run: ExtractionRunRecord
    schema: SchemaRecord
    records: list[ExtractedRecordRow]
    fields: list[GenericFieldRow]


@dataclass(frozen=True)
class ExtractionReview:
    run: ExtractionRunRecord
    schema: SchemaRecord
    document: DocumentRecord
    hierarchy: dict[str, Any]
    fields: list[GenericFieldRow]


@dataclass(frozen=True)
class ExtractionRunPage:
    items: list[ExtractionRunListRecord]
    next_cursor: str | None


class ExtractionResultsService:
    def __init__(
        self,
        extraction_runs: ExtractionRunRepository,
        schemas: SchemaRepository,
        documents: DocumentRegistry,
    ) -> None:
        self._runs = extraction_runs
        self._schemas = schemas
        self._documents = documents

    async def list_page(
        self,
        *,
        case_id: str | None = None,
        document_id: str | None = None,
        schema_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
        latest_only: bool = True,
        limit: int = 50,
        cursor: str | None = None,
    ) -> ExtractionRunPage:
        """Return one bounded Results page through one repository query.

        The repository owns the join, filtering, latest-run calculation and cursor predicate so
        this service cannot accidentally rebuild a list from detail-shaped repository methods.
        """
        cursor_started_at, cursor_run_id = _decode_cursor(cursor) if cursor else (None, None)
        rows = await run_in_threadpool(
            self._runs.list_result_page,
            limit=limit + 1,
            cursor_started_at=cursor_started_at,
            cursor_run_id=cursor_run_id,
            case_id=case_id,
            document_id=document_id,
            schema_id=schema_id,
            status=status,
            search=search,
            latest_only=latest_only,
        )
        has_more = len(rows) > limit
        items = rows[:limit]
        return ExtractionRunPage(
            items=items,
            next_cursor=_encode_cursor(items[-1]) if has_more and items else None,
        )

    async def document_name(self, document_id: str) -> str:
        document = await run_in_threadpool(self._documents.get, document_id)
        return document.file_name if document else document_id

    async def get_result(self, extraction_run_id: str) -> GenericExtractionResult:
        run, schema = await self._load(extraction_run_id)
        response = run.ai_result.get("response") if run.ai_result else None
        return GenericExtractionResult(
            run=run, schema=schema, hierarchy=response if isinstance(response, dict) else {}
        )

    async def get_records(self, extraction_run_id: str) -> GenericExtractionRecords:
        run, schema = await self._load(extraction_run_id)
        if run.ai_result is None:
            raise DocumentServiceError(
                "EXTRACTION_RESULT_UNAVAILABLE",
                "This extraction run has no retained ai_extract result.",
                409,
            )
        records, fields = await self._records_and_fields(run, schema)
        return GenericExtractionRecords(run=run, schema=schema, records=records, fields=fields)

    async def get_review(self, extraction_run_id: str) -> ExtractionReview:
        """Load the complete review workspace without duplicate run/schema reads or writes."""
        run, schema = await self._load(extraction_run_id)
        if run.ai_result is None:
            raise DocumentServiceError(
                "EXTRACTION_RESULT_UNAVAILABLE",
                "This extraction run has no retained ai_extract result.",
                409,
            )
        document = await run_in_threadpool(self._documents.get, run.document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        _records, fields = await self._records_and_fields(run, schema)
        response = run.ai_result.get("response")
        return ExtractionReview(
            run=run,
            schema=schema,
            document=document,
            hierarchy=response if isinstance(response, dict) else {},
            fields=fields,
        )

    async def _records_and_fields(
        self, run: ExtractionRunRecord, schema: SchemaRecord
    ) -> tuple[list[ExtractedRecordRow], list[GenericFieldRow]]:
        """Read retained projections, with a read-only in-memory fallback for legacy runs."""
        try:
            persisted_records = await run_in_threadpool(
                self._runs.list_generic_records, run.extraction_run_id
            )
            persisted_fields = (
                await run_in_threadpool(self._runs.list_generic_fields, run.extraction_run_id)
                if persisted_records
                else []
            )
            # A cache hit requires *both*: an environment where extracted_fields can be
            # written might never actually persist fields (e.g. the write is unavailable),
            # in which case records alone are not enough -- returning them with an empty
            # fields list would silently drop every field's confidence/citation/validation
            # data. Treat that as a miss and recompute the whole result instead.
            if persisted_records and persisted_fields:
                return persisted_records, persisted_fields
        except Exception:
            _logger.warning(
                "Could not read the persisted generic record tree for run %s; falling "
                "back to recomputing it from the retained ai_extract result.",
                run.extraction_run_id,
                exc_info=True,
            )
        if run.ai_result is None:
            return [], []
        return await run_in_threadpool(walk_extraction, run, schema, run.ai_result)

    async def _load(self, extraction_run_id: str) -> tuple[ExtractionRunRecord, SchemaRecord]:
        run = await run_in_threadpool(self._runs.get, extraction_run_id)
        if run is None:
            raise DocumentServiceError(
                "EXTRACTION_RUN_NOT_FOUND", "Extraction run not found.", 404
            )
        schema = await run_in_threadpool(self._schemas.get, run.schema_id, run.schema_version)
        if schema is None:
            raise DocumentServiceError(
                "SCHEMA_NOT_FOUND",
                "The schema version used by this extraction run is no longer available.",
                404,
            )
        return run, schema

def _encode_cursor(row: ExtractionRunListRecord) -> str:
    payload = f"{row.started_at.isoformat()}\n{row.extraction_run_id}".encode()
    return base64.urlsafe_b64encode(payload).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        started_at_text, run_id = decoded.split("\n", maxsplit=1)
        started_at = datetime.fromisoformat(started_at_text.replace("Z", "+00:00"))
    except (UnicodeDecodeError, ValueError, binascii.Error) as error:
        raise DocumentServiceError(
            "RESULT_CURSOR_INVALID", "Results cursor is invalid.", 422
        ) from error
    if not run_id:
        raise DocumentServiceError("RESULT_CURSOR_INVALID", "Results cursor is invalid.", 422)
    return (started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC), run_id)
