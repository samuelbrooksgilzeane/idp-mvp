"""Generic (schema-agnostic) extraction results: section 6 of the generalized IDP plan.

The recursive record tree (`extracted_records` and the generic columns on
`extracted_fields`) is a write-through cache: the first time a run's records are read (via
`get_records`), `walk_extraction` computes them from the already-retained raw `ai_extract`
response, exactly as before, and the result is persisted so every later read of that run hits
the tables directly instead of recomputing. Nothing in the extraction pipeline itself (the
Databricks job, the mock job runner) writes these tables -- only a read through this service
ever does, so `walk_extraction` (already tested) remains the only thing that ever produces
this data. A persistence failure never fails the read: it degrades back to recomputing on
every call, not to an error.

`list_summaries` deliberately does *not* go through that cache. It reads only bulk aggregates,
because a cache miss costs a recompute and the list would pay for one per run: where the cache
cannot be written at all (an environment that grants the app SELECT but not MODIFY on
`extracted_fields`), every run missed on every load and the endpoint timed out at the gateway
instead of returning a list.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import (
    ExtractedRecordRow,
    ExtractionRunRecord,
    FieldIssueSignal,
    GenericFieldRow,
)
from idp_app.services.document_registry import DocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_result import walk_extraction
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.schema_models import FieldPolicy, SchemaRecord
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
        schema context for the Results list.

        Every read here is bulk: the number of queries is fixed by the number of *distinct
        schema versions*, not by the number of runs. Counting a run by materialising its
        record tree (as `get_records` does) costs two round trips per run plus a recompute
        whenever the tree is not cached, which on a SQL warehouse is seconds each and made
        this endpoint time out at the gateway rather than return a slow list.
        """
        runs = await run_in_threadpool(self._runs.list_all_metadata)
        documents_by_id = {
            document.document_id: document
            for document in await run_in_threadpool(self._documents.list_documents)
        }
        root_records = await run_in_threadpool(self._runs.count_root_records)
        signals_by_run: dict[str, list[FieldIssueSignal]] = defaultdict(list)
        for signal in await run_in_threadpool(self._runs.list_field_issue_signals):
            signals_by_run[signal.run_id].append(signal)
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
            signals = signals_by_run.get(run.extraction_run_id, [])
            # `walk_extraction` roots every run's tree at exactly one record, so a run that
            # produced any leaf has one root whether or not its tree has been walked and
            # cached yet. Prefer the persisted count; fall back to that invariant rather than
            # reporting a freshly extracted run as empty until someone opens it.
            records_count = root_records.get(run.extraction_run_id) or (1 if signals else 0)
            issues_count = _count_signal_issues(signals, schema) if schema is not None else 0
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
        records, fields = await self._records_and_fields(run, schema)
        return GenericExtractionRecords(run=run, schema=schema, records=records, fields=fields)

    async def _records_and_fields(
        self, run: ExtractionRunRecord, schema: SchemaRecord
    ) -> tuple[list[ExtractedRecordRow], list[GenericFieldRow]]:
        """Read-through cache over the recursive record tree: try the persisted tables
        first, and only fall back to recomputing from the raw `ai_extract` response (via
        `walk_extraction`) on a cache miss -- the run's first read, one extracted before this
        cache existed, or an environment where writing extracted_fields is unavailable (in
        which case records get cached but fields never do, and every read still recomputes
        fields -- see the guard below). A computed result is persisted so every later read is
        cheap wherever both tables are writable; a persistence failure degrades back to always
        recomputing, never to an error.
        """
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
        records, fields = await run_in_threadpool(walk_extraction, run, schema, run.ai_result)
        try:
            await run_in_threadpool(self._runs.persist_generic, records, fields)
        except Exception:
            _logger.warning(
                "Could not persist the generic record tree for run %s; it will be "
                "recomputed on the next read.",
                run.extraction_run_id,
                exc_info=True,
            )
        return records, fields

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


_INSTANCE_INDEX = re.compile(r"\[\d+\]")


def _policy_key(path: str) -> str:
    """`field_policies` is keyed by the schema's `[*]` wildcard convention (`schema_leaves`,
    `line_items[*].amount`). The walker's `schema_path` uses `[]` for the same thing, and the
    flattened `field_path` carries a concrete index (`line_items[0].amount`). All three
    describe the same leaf, so every lookup goes through this one conversion.
    """
    return _INSTANCE_INDEX.sub("[*]", path).replace("[]", "[*]")


def _is_issue(policy: FieldPolicy, confidence_score: float | None, has_citation: bool) -> bool:
    """A field is an "issue" when its confidence falls below the policy threshold for that
    leaf, or a citation is required but none was returned. Both counters below apply this one
    rule, so a list row and its detail view can never disagree about what an issue is.
    """
    low_confidence = (
        confidence_score is not None and confidence_score < policy.confidence_threshold
    )
    return low_confidence or (policy.citation_required and not has_citation)


def _count_issues(fields: list[GenericFieldRow], schema: SchemaRecord) -> int:
    """Issue count over a materialised record tree, for the detail view."""
    issues = 0
    for entry in fields:
        policy = schema.field_policies.get(_policy_key(entry.schema_path))
        if policy is not None and _is_issue(policy, entry.confidence_score, bool(entry.citations)):
            issues += 1
    return issues


def _count_signal_issues(signals: list[FieldIssueSignal], schema: SchemaRecord) -> int:
    """Issue count over the flattened `extracted_fields` rows, for the Results list.

    The extraction job writes one row per scalar leaf, exactly as the walker emits one field
    per scalar leaf, so this counts the same leaves as `_count_issues` without rebuilding the
    tree -- and, unlike the record tree, these rows exist for every run the job completed.
    """
    issues = 0
    for signal in signals:
        policy = schema.field_policies.get(_policy_key(signal.field_path))
        if policy is not None and _is_issue(policy, signal.confidence_score, signal.has_citation):
            issues += 1
    return issues
