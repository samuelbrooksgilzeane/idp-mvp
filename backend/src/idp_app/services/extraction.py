from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from databricks.sdk.errors import (
    BadRequest,
    InvalidParameterValue,
    NotFound,
    PermissionDenied,
    Unauthenticated,
)
from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import (
    DocumentRecord,
    ExtractedFieldRecord,
    ExtractionRunRecord,
    InvoiceCandidateRecord,
)
from idp_app.services.document_registry import DocumentRegistry, InvalidDocumentStateError
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_inputs import (
    ELIGIBLE_DOCUMENT_STATES,
    ExtractionInputs,
    published_schema,
    resolve_extraction_inputs,
)
from idp_app.services.extraction_jobs import (
    ExtractionJobRequest,
    ExtractionJobRunner,
    ExtractionJobState,
)
from idp_app.services.extraction_runs import ExtractionRunRepository
from idp_app.services.job_batches import BatchFailure
from idp_app.services.parse_runs import ParseRunRepository
from idp_app.services.schema_models import SchemaRecord
from idp_app.services.schema_registry import SchemaRepository

EXTRACTOR_VERSION = "2.1"
logger = logging.getLogger(__name__)
# A submission Jobs never recorded within this time was not accepted; Jobs records a run at once.
SUBMISSION_GRACE = timedelta(minutes=10)
# A claim whose run row was never written (the process stopped in between) is released after this.
CLAIM_GRACE = timedelta(minutes=2)
# The request was rejected, so no job can exist. Anything else (timeouts, 5xx) may have started one.
REJECTED = (
    ValueError,
    BadRequest,
    InvalidParameterValue,
    NotFound,
    PermissionDenied,
    Unauthenticated,
)


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
        prepared = await self._prepare(document_id, schema_id, schema_version, requested_by)
        await self._submit([prepared])
        created = await run_in_threadpool(self._runs.get, prepared[0].extraction_run_id)
        if created is None:
            raise RuntimeError("Created extraction run could not be loaded")
        return created

    async def start_batch(
        self,
        document_ids: list[str],
        schema_id: str,
        schema_version: int,
        requested_by: str,
    ) -> tuple[list[ExtractionRunRecord], list[BatchFailure]]:
        """Prepare every eligible document, then submit them as one job run.

        A document that fails its own preconditions is reported against that document and does
        not prevent the rest of the batch from running.
        """
        identities = list(dict.fromkeys(document_ids))
        try:
            schema = await run_in_threadpool(
                published_schema, self._schemas, schema_id, schema_version
            )
        except DocumentServiceError as error:
            return [], [
                BatchFailure(document_id=identity, code=error.code, message=error.message)
                for identity in identities
            ]
        inputs, failures = await self._resolve(schema, identities)
        prepared: list[tuple[ExtractionRunRecord, ExtractionJobRequest]] = []
        for resolved in inputs:
            try:
                prepared.append(await self._prepare_resolved(resolved, requested_by))
            except DocumentServiceError as error:
                failures.append(
                    BatchFailure(
                        document_id=resolved.document.document_id,
                        code=error.code,
                        message=error.message,
                    )
                )
        if not prepared:
            return [], failures

        await self._submit(prepared)
        runs: list[ExtractionRunRecord] = []
        for run, _ in prepared:
            created = await run_in_threadpool(self._runs.get, run.extraction_run_id)
            if created is not None:
                runs.append(created)
        return runs, failures

    async def _prepare(
        self,
        document_id: str,
        schema_id: str,
        schema_version: int,
        requested_by: str,
    ) -> tuple[ExtractionRunRecord, ExtractionJobRequest]:
        schema = await run_in_threadpool(published_schema, self._schemas, schema_id, schema_version)
        inputs, failures = await self._resolve(schema, [document_id])
        if failures:
            failure = failures[0]
            raise DocumentServiceError(
                failure.code,
                failure.message,
                404 if failure.code == "DOCUMENT_NOT_FOUND" else 409,
                document_id=document_id,
            )
        return await self._prepare_resolved(inputs[0], requested_by)

    async def _resolve(
        self, schema: SchemaRecord, document_ids: list[str]
    ) -> tuple[list[ExtractionInputs], list[BatchFailure]]:
        """Extraction inputs, after settling any stranded claim that makes a document look busy.
        Only documents already refused as not extractable are looked at again."""
        inputs, failures = await run_in_threadpool(
            resolve_extraction_inputs, self._documents, self._parse_runs, schema, document_ids
        )
        released = []
        for failure in failures:
            if failure.code != "DOCUMENT_NOT_EXTRACTABLE":
                continue
            document = await run_in_threadpool(self._documents.get, failure.document_id)
            if document is not None and await self._release_stranded(document):
                released.append(failure.document_id)
        if not released:
            return inputs, failures
        more_inputs, more_failures = await run_in_threadpool(
            resolve_extraction_inputs, self._documents, self._parse_runs, schema, released
        )
        kept = [failure for failure in failures if failure.document_id not in released]
        return [*inputs, *more_inputs], [*kept, *more_failures]

    async def _prepare_resolved(
        self, inputs: ExtractionInputs, requested_by: str
    ) -> tuple[ExtractionRunRecord, ExtractionJobRequest]:
        document, parse_run, schema = inputs.document, inputs.parse, inputs.schema
        document_id = document.document_id
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

        # The claim names this run as the document's owner, so everything written about the
        # extraction afterwards is conditional on it (see DocumentRegistry.begin_extraction).
        for attempt in range(2):
            try:
                await run_in_threadpool(
                    self._documents.begin_extraction,
                    document_id,
                    ELIGIBLE_DOCUMENT_STATES,
                    schema.schema_id,
                    schema.schema_version,
                    extraction_run_id,
                )
                break
            except InvalidDocumentStateError as error:
                if attempt == 0 and await self._release_stranded(error.document):
                    continue  # A stranded claim was released; claim again.
                raise DocumentServiceError(
                    "DOCUMENT_NOT_EXTRACTABLE",
                    f"Document cannot be extracted from status {error.document.status}.",
                    409,
                    document_id=document_id,
                ) from error

        try:
            await run_in_threadpool(self._runs.create, run)
        except Exception:
            # The write may have landed before its answer was lost. Without the run row, nothing
            # could ever settle the claim, so release it.
            if await run_in_threadpool(self._runs.get, extraction_run_id) is None:
                await self._release(document_id, extraction_run_id, "EXTRACT_FAILED")
                raise
        return run, ExtractionJobRequest(run, document, parse_run, schema)

    async def _submit(
        self, prepared: list[tuple[ExtractionRunRecord, ExtractionJobRequest]]
    ) -> None:
        """Submit every prepared document as a single job run and record its identifier.

        Acceptance and recording are separate. Only a rejected submission fails the runs; when
        the outcome is unknown, or the job id cannot be recorded, the runs stay RUNNING and
        reads find the job again by run id (`_reconcile`), so an accepted job can still commit.
        """
        requests = [request for _, request in prepared]
        try:
            job_run_id = await run_in_threadpool(self._trigger, requests)
        except REJECTED as error:
            for run, _ in prepared:
                await self._fail_running(run, "Extraction job could not be started.")
            raise DocumentServiceError(
                "EXTRACTION_JOB_TRIGGER_FAILED",
                "The extraction job could not be started.",
                502,
                document_id=prepared[0][0].document_id if len(prepared) == 1 else None,
            ) from error
        except Exception:
            logger.warning("Extraction job submission outcome unknown; reads will reconcile it")
            return
        for run, _ in prepared:
            try:
                await run_in_threadpool(
                    _with_retries, self._runs.assign_job_run, run.extraction_run_id, job_run_id
                )
            except Exception:
                logger.warning("Extraction job %s accepted but not recorded yet", job_run_id)

    def _trigger(self, requests: list[ExtractionJobRequest]) -> int:
        # The same runs give the same idempotency token, so repeating cannot start a second job.
        for attempt in range(3):
            try:
                return self._jobs.trigger(requests)
            except REJECTED:
                raise
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.5 * 2**attempt)
        raise AssertionError("unreachable")

    async def _release(self, document_id: str, owner: str | None, status: str) -> None:
        """Settle a claim, only while `owner` still holds it."""
        with suppress(InvalidDocumentStateError, KeyError):
            await run_in_threadpool(
                self._documents.update_status, document_id, {"EXTRACTING"}, status, owner
            )

    async def _release_stranded(self, document: DocumentRecord) -> bool:
        """Settle a claim nothing else will settle; True if the document is no longer claimed.

        Bounded to the one document a request is about, and never touches a claim that a live
        submission or the work queue (which has its own leases) can still finish."""
        if document.status != "EXTRACTING":
            return False
        owner = document.extraction_run_id
        if owner is None:
            # A claim from before owner tokens: its latest run, if any, is the owner.
            runs = await run_in_threadpool(self._runs.list_for_document, document.document_id)
            run = runs[0] if runs else None
        else:
            run = await run_in_threadpool(self._runs.get, owner)
        if run is None:
            if datetime.now(UTC) - document.updated_at < CLAIM_GRACE:
                return False  # The claimant may still be writing its run.
            await self._release(document.document_id, owner, "EXTRACT_FAILED")
        elif run.options.get("work_item_id"):
            return False
        elif run.status == "RUNNING":
            await self._reconcile(run)
        else:
            settled = "EXTRACTED" if run.status == "EXTRACTED" else "EXTRACT_FAILED"
            await self._release(document.document_id, run.extraction_run_id, settled)
        current = await run_in_threadpool(self._documents.get, document.document_id)
        return current is not None and current.status != "EXTRACTING"

    async def batch(self, job_run_id: int) -> list[ExtractionRunRecord]:
        """Every immutable run submitted under one job run, refreshed to a terminal state.

        A per-document task records its own outcome, so the job state only settles runs that
        never committed one.
        """
        runs = await run_in_threadpool(self._runs.list_for_job_run, job_run_id)
        if not runs:
            raise DocumentServiceError("BATCH_NOT_FOUND", "Batch not found.", 404)
        if any(run.status == "RUNNING" and not run.options.get("work_item_id") for run in runs):
            poll = await run_in_threadpool(self._jobs.poll, job_run_id)
            if poll.state is not ExtractionJobState.RUNNING:
                for run in runs:
                    refreshed = await run_in_threadpool(self._runs.get, run.extraction_run_id)
                    if (
                        refreshed
                        and refreshed.status == "RUNNING"
                        and not refreshed.options.get("work_item_id")
                    ):
                        await self._fail_running(
                            refreshed, poll.message or "Extraction job failed."
                        )
                runs = await run_in_threadpool(self._runs.list_for_job_run, job_run_id)
        return runs

    async def list_runs(self, document_id: str) -> list[ExtractionRunRecord]:
        # Independent reads: the history is only returned once the document is confirmed.
        document, runs = await asyncio.gather(
            run_in_threadpool(self._documents.get, document_id),
            run_in_threadpool(self._runs.list_for_document, document_id),
        )
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        refreshed = False
        for run in runs:
            if run.status == "RUNNING" and not run.options.get("work_item_id"):
                await self._reconcile(run)
                refreshed = True
        if refreshed:
            return await run_in_threadpool(self._runs.list_for_document, document_id)
        return runs

    async def latest(
        self, document_id: str
    ) -> tuple[ExtractionRunRecord, list[ExtractedFieldRecord], list[InvoiceCandidateRecord]]:
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
        candidates = await run_in_threadpool(self._runs.list_candidates, run.extraction_run_id)
        return run, fields, candidates

    async def result(
        self, document_id: str, extraction_run_id: str
    ) -> tuple[ExtractionRunRecord, list[ExtractedFieldRecord], list[InvoiceCandidateRecord]]:
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
        candidates = await run_in_threadpool(self._runs.list_candidates, run.extraction_run_id)
        return run, fields, candidates

    async def _reconcile(self, run: ExtractionRunRecord) -> None:
        if run.options.get("work_item_id"):
            return  # Manifest Jobs are reconciled centrally, never by browser reads.
        if run.job_run_id is None:
            # Submitted, but the job id was never recorded: find the job by run id.
            job_run_id = await run_in_threadpool(
                self._jobs.find, run.extraction_run_id, run.started_at
            )
            if job_run_id is None:
                if datetime.now(UTC) - run.started_at > SUBMISSION_GRACE:
                    await self._fail_running(run, "The extraction job was never started.")
                return
            await run_in_threadpool(self._runs.assign_job_run, run.extraction_run_id, job_run_id)
            run = replace(run, job_run_id=job_run_id)
        assert run.job_run_id is not None
        poll = await run_in_threadpool(self._jobs.poll, run.job_run_id)
        if poll.state is ExtractionJobState.FAILED:
            await self._fail_running(run, poll.message or "Extraction job failed.")
        elif poll.state is ExtractionJobState.SUCCEEDED:
            refreshed = await run_in_threadpool(self._runs.get, run.extraction_run_id)
            if (
                refreshed
                and refreshed.status == "RUNNING"
                and not refreshed.options.get("work_item_id")
            ):
                await self._fail_running(
                    refreshed,
                    "Extraction job completed without committing a terminal result.",
                )

    async def _fail_running(self, run: ExtractionRunRecord, message: str) -> None:
        await run_in_threadpool(self._runs.fail, run.extraction_run_id, message[:500])
        await self._release(run.document_id, run.extraction_run_id, "EXTRACT_FAILED")


def _with_retries(write: Callable[..., None], *args: object) -> None:
    for attempt in range(3):
        try:
            write(*args)
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.5 * 2**attempt)


def extraction_idempotency_key(
    document_id: str,
    parse_run_id: str,
    schema_id: str,
    schema_version: int,
    extractor_version: str,
) -> str:
    return "+".join((document_id, parse_run_id, schema_id, str(schema_version), extractor_version))
