from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import (
    ExtractedFieldRecord,
    ExtractionRunRecord,
    InvoiceCandidateRecord,
)
from idp_app.services.document_registry import DocumentRegistry, InvalidDocumentStateError
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_jobs import (
    ExtractionJobRequest,
    ExtractionJobRunner,
    ExtractionJobState,
)
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.parse_runs import ParseRunRepository
from idp_app.services.schema_registry import SchemaRepository

EXTRACTOR_VERSION = "2.1"
ELIGIBLE_DOCUMENT_STATES = {
    "PARSED",
    "EXTRACTED",
    "EXTRACT_FAILED",
    "VALIDATED_PASS",
    "REVIEW_REQUIRED",
}


class ExtractionService:
    def __init__(
        self,
        documents: DocumentRegistry,
        parse_runs: ParseRunRepository,
        schemas: SchemaRepository,
        extraction_runs: ExtractionRunRepository,
        jobs: ExtractionJobRunner,
    ) -> None:
        self._documents = documents
        self._parse_runs = parse_runs
        self._schemas = schemas
        self._runs = extraction_runs
        self._jobs = jobs

    async def start(
        self,
        document_id: str,
        schema_id: str,
        schema_version: int,
        requested_by: str,
    ) -> ExtractionRunRecord:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        parse_run = await run_in_threadpool(self._parse_runs.latest_successful, document_id)
        if parse_run is None:
            raise DocumentServiceError(
                "SUCCESSFUL_PARSE_REQUIRED",
                "A successful parse is required before extraction.",
                409,
                document_id=document_id,
            )
        schema = await run_in_threadpool(self._schemas.get, schema_id, schema_version)
        if schema is None:
            raise DocumentServiceError("SCHEMA_NOT_FOUND", "Extraction schema not found.", 404)
        if schema.status != "PRODUCTION":
            raise DocumentServiceError(
                "SCHEMA_NOT_PRODUCTION", "Only a production schema can be extracted.", 409
            )
        if schema.use_case != document.use_case:
            raise DocumentServiceError(
                "SCHEMA_USE_CASE_MISMATCH",
                "The extraction schema does not match the document use case.",
                409,
                document_id=document_id,
            )

        extraction_run_id = str(uuid4())
        options = {
            "version": EXTRACTOR_VERSION,
            "mode": "precision",
            "enableCitations": "true",
            "enableConfidenceScores": "true",
            "idempotency_key": extraction_idempotency_key(
                document_id,
                parse_run.parse_run_id,
                schema.schema_id,
                schema.schema_version,
                EXTRACTOR_VERSION,
            ),
        }
        run = ExtractionRunRecord(
            extraction_run_id=extraction_run_id,
            document_id=document_id,
            parse_run_id=parse_run.parse_run_id,
            schema_id=schema.schema_id,
            schema_version=schema.schema_version,
            schema_hash=schema.schema_hash,
            extractor_version=EXTRACTOR_VERSION,
            options=options,
            ai_result=None,
            error_message=None,
            status="RUNNING",
            requested_by=requested_by,
            job_run_id=None,
            started_at=datetime.now(UTC),
            completed_at=None,
        )

        try:
            await run_in_threadpool(
                self._documents.begin_extraction,
                document_id,
                ELIGIBLE_DOCUMENT_STATES,
                schema.schema_id,
                schema.schema_version,
            )
        except InvalidDocumentStateError as error:
            raise DocumentServiceError(
                "DOCUMENT_NOT_EXTRACTABLE",
                f"Document cannot be extracted from status {error.document.status}.",
                409,
                document_id=document_id,
            ) from error

        try:
            await run_in_threadpool(self._runs.create, run)
            job_run_id = await run_in_threadpool(
                self._jobs.trigger,
                ExtractionJobRequest(run, document, parse_run, schema),
            )
            await run_in_threadpool(self._runs.assign_job_run, extraction_run_id, job_run_id)
        except Exception as error:
            current = await run_in_threadpool(self._runs.get, extraction_run_id)
            if current and current.status == "RUNNING":
                await run_in_threadpool(
                    self._runs.fail, extraction_run_id, "Extraction job could not be started."
                )
            current_document = await run_in_threadpool(self._documents.get, document_id)
            if current_document and current_document.status == "EXTRACTING":
                await run_in_threadpool(
                    self._documents.update_status,
                    document_id,
                    {"EXTRACTING"},
                    "EXTRACT_FAILED",
                )
            raise DocumentServiceError(
                "EXTRACTION_JOB_TRIGGER_FAILED",
                "The extraction job could not be started.",
                502,
                document_id=document_id,
            ) from error
        created = await run_in_threadpool(self._runs.get, extraction_run_id)
        if created is None:
            raise RuntimeError("Created extraction run could not be loaded")
        return created

    async def list_runs(self, document_id: str) -> list[ExtractionRunRecord]:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        runs = await run_in_threadpool(self._runs.list_for_document, document_id)
        for run in runs:
            if run.status == "RUNNING" and run.job_run_id is not None:
                await self._refresh(run)
        return await run_in_threadpool(self._runs.list_for_document, document_id)

    async def latest(
        self, document_id: str
    ) -> tuple[ExtractionRunRecord, list[ExtractedFieldRecord], InvoiceCandidateRecord | None]:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        run = await run_in_threadpool(self._runs.latest_successful, document_id)
        if run is None:
            raise DocumentServiceError(
                "SUCCESSFUL_EXTRACTION_NOT_FOUND",
                "No successful extraction exists for this document.",
                404,
                document_id=document_id,
            )
        fields = await run_in_threadpool(self._runs.list_fields, run.extraction_run_id)
        candidate = await run_in_threadpool(self._runs.get_candidate, run.extraction_run_id)
        return run, fields, candidate

    async def result(
        self, document_id: str, extraction_run_id: str
    ) -> tuple[ExtractionRunRecord, list[ExtractedFieldRecord], InvoiceCandidateRecord | None]:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        run = await run_in_threadpool(self._runs.get, extraction_run_id)
        if run is None or run.document_id != document_id:
            raise DocumentServiceError(
                "EXTRACTION_RUN_NOT_FOUND",
                "Extraction run not found for this document.",
                404,
                document_id=document_id,
            )
        fields = await run_in_threadpool(self._runs.list_fields, run.extraction_run_id)
        candidate = await run_in_threadpool(self._runs.get_candidate, run.extraction_run_id)
        return run, fields, candidate

    async def _refresh(self, run: ExtractionRunRecord) -> None:
        assert run.job_run_id is not None
        poll = await run_in_threadpool(self._jobs.poll, run.job_run_id)
        if poll.state is ExtractionJobState.FAILED:
            await self._fail_running(run, poll.message or "Extraction job failed.")
        elif poll.state is ExtractionJobState.SUCCEEDED:
            refreshed = await run_in_threadpool(self._runs.get, run.extraction_run_id)
            if refreshed and refreshed.status == "RUNNING":
                await self._fail_running(
                    refreshed,
                    "Extraction job completed without committing a terminal result.",
                )

    async def _fail_running(self, run: ExtractionRunRecord, message: str) -> None:
        await run_in_threadpool(self._runs.fail, run.extraction_run_id, message[:500])
        document = await run_in_threadpool(self._documents.get, run.document_id)
        if document and document.status == "EXTRACTING":
            await run_in_threadpool(
                self._documents.update_status,
                run.document_id,
                {"EXTRACTING"},
                "EXTRACT_FAILED",
            )


def extraction_idempotency_key(
    document_id: str,
    parse_run_id: str,
    schema_id: str,
    schema_version: int,
    extractor_version: str,
) -> str:
    return "+".join(
        (document_id, parse_run_id, schema_id, str(schema_version), extractor_version)
    )
