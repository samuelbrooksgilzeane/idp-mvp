"""Generic (schema-agnostic) extraction results: section 6 of the generalized IDP plan.

Rather than persisting a second copy of the recursive result, this recomputes it on demand
from the already-retained raw `ai_extract` response and the schema used to produce it -- both
of which are already stored on the immutable extraction run for auditing. This keeps the
generalization additive: no new write path is introduced into the extraction pipeline, and the
result is always in lockstep with what was actually returned by `ai_extract`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import (
    ExtractedRecordRow,
    ExtractionRunRecord,
    GenericFieldRow,
)
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_result import walk_extraction
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.schema_models import SchemaRecord
from idp_app.services.schema_registry import SchemaRepository


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
class ExtractionRunSummary:
    """One row of the run-centric Results list: a run plus enough of its document and schema
    context to display and filter without a second round trip per row."""

    run: ExtractionRunRecord
    document_name: str
    case_id: str | None
    schema_display_name: str
    is_latest: bool
    records_count: int
    issues_count: int


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

    async def list_summaries(
        self,
        *,
        case_id: str | None = None,
        document_id: str | None = None,
        schema_id: str | None = None,
        status: str | None = None,
    ) -> list[ExtractionRunSummary]:
        """Every extraction run across every document, joined with just enough document and
        schema context for the Results list -- no persisted read model, computed on demand
        from the same retained data `get_records` already uses.
        """
        runs = await run_in_threadpool(self._runs.list_all)
        documents_by_id = {
            document.document_id: document
            for document in await run_in_threadpool(self._documents.list_documents)
        }
        schema_cache: dict[tuple[str, int], SchemaRecord | None] = {}

        async def _schema_for(schema_id_: str, schema_version: int) -> SchemaRecord | None:
            key = (schema_id_, schema_version)
            if key not in schema_cache:
                schema_cache[key] = await run_in_threadpool(
                    self._schemas.get, schema_id_, schema_version
                )
            return schema_cache[key]

        latest_started_at: dict[tuple[str, str], datetime] = {}
        for run in runs:
            key = (run.document_id, run.schema_id)
            if key not in latest_started_at or run.started_at > latest_started_at[key]:
                latest_started_at[key] = run.started_at

        summaries: list[ExtractionRunSummary] = []
        for run in runs:
            document = documents_by_id.get(run.document_id)
            if document is None:
                continue
            if case_id is not None and document.case_id != case_id:
                continue
            if document_id is not None and run.document_id != document_id:
                continue
            if schema_id is not None and run.schema_id != schema_id:
                continue
            if status is not None and run.status != status:
                continue
            schema = await _schema_for(run.schema_id, run.schema_version)
            records_count, issues_count = 0, 0
            if run.ai_result is not None and schema is not None:
                records, fields = await run_in_threadpool(
                    walk_extraction, run, schema, run.ai_result
                )
                records_count = sum(1 for record in records if record.parent_record_id is None)
                issues_count = _count_issues(fields, schema)
            summaries.append(
                ExtractionRunSummary(
                    run=run,
                    document_name=document.file_name,
                    case_id=document.case_id,
                    schema_display_name=schema.display_name if schema else run.schema_id,
                    is_latest=latest_started_at.get((run.document_id, run.schema_id))
                    == run.started_at,
                    records_count=records_count,
                    issues_count=issues_count,
                )
            )
        return summaries

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
        records, fields = walk_extraction(run, schema, run.ai_result)
        return GenericExtractionRecords(run=run, schema=schema, records=records, fields=fields)

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


def _count_issues(fields: list[GenericFieldRow], schema: SchemaRecord) -> int:
    """A field is an "issue" when its confidence falls below the policy threshold for that
    leaf, or a citation is required but none was returned. `GenericFieldRow.schema_path` uses
    the walker's `[]` convention (e.g. `line_items[].amount`); `field_policies` is keyed by the
    schema's own `[*]` wildcard convention (`schema_leaves`, `line_items[*].amount`) -- same
    structure, different bracket fill, so the lookup converts one to the other.
    """
    issues = 0
    for entry in fields:
        policy = schema.field_policies.get(entry.schema_path.replace("[]", "[*]"))
        if policy is None:
            continue
        low_confidence = (
            entry.confidence_score is not None
            and entry.confidence_score < policy.confidence_threshold
        )
        if low_confidence or (policy.citation_required and not entry.citations):
            issues += 1
    return issues
