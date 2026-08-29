from datetime import UTC, datetime
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from idp_app.core.config import IdpMode, Settings
from idp_app.services.document_models import ParseRunRecord
from idp_app.services.document_registry import (
    DocumentRegistry,
    InvalidDocumentStateError,
)
from idp_app.services.documents import DocumentServiceError
from idp_app.services.job_batches import BatchFailure
from idp_app.services.parse_jobs import (
    ParseJobRequest,
    ParseJobRunner,
    ParseJobState,
)
from idp_app.services.parse_runs import ParseRunRepository

PARSER_VERSION = "2.0"
ELIGIBLE_DOCUMENT_STATES = {
    "UPLOADED",
    "PARSE_FAILED",
    "PARSED",
    "EXTRACTED",
    "EXTRACT_FAILED",
    "VALIDATED_PASS",
    "REVIEW_REQUIRED",
}


class ParsingService:
    def __init__(
        self,
        settings: Settings,
        documents: DocumentRegistry,
        parse_runs: ParseRunRepository,
        jobs: ParseJobRunner,
    ) -> None:
        self._settings = settings
        self._documents = documents
        self._parse_runs = parse_runs
        self._jobs = jobs

    async def start(self, document_id: str, requested_by: str) -> ParseRunRecord:
        prepared = await self._prepare(document_id, requested_by)
        await self._submit([prepared])
        created = await run_in_threadpool(self._parse_runs.get, prepared[0].parse_run_id)
        if created is None:
            raise RuntimeError("Created parse run could not be loaded")
        return created

    async def start_batch(
        self, document_ids: list[str], requested_by: str
    ) -> tuple[list[ParseRunRecord], list[BatchFailure]]:
        """Prepare every eligible document, then submit them as one job run.

        A document that fails its own preconditions is reported against that document and does
        not prevent the rest of the batch from running.
        """
        prepared: list[tuple[ParseRunRecord, ParseJobRequest]] = []
        failures: list[BatchFailure] = []
        for document_id in dict.fromkeys(document_ids):
            try:
                prepared.append(await self._prepare(document_id, requested_by))
            except DocumentServiceError as error:
                failures.append(
                    BatchFailure(document_id=document_id, code=error.code, message=error.message)
                )
        if not prepared:
            return [], failures

        await self._submit(prepared)
        runs: list[ParseRunRecord] = []
        for run, _ in prepared:
            created = await run_in_threadpool(self._parse_runs.get, run.parse_run_id)
            if created is not None:
                runs.append(created)
        return runs, failures

    async def _prepare(
        self, document_id: str, requested_by: str
    ) -> tuple[ParseRunRecord, ParseJobRequest]:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        if document.status not in ELIGIBLE_DOCUMENT_STATES:
            raise DocumentServiceError(
                "DOCUMENT_NOT_PARSEABLE",
                f"Document cannot be parsed from status {document.status}.",
                409,
            )

        parse_run_id = str(uuid4())
        page_image_root = self._page_image_root(document_id, parse_run_id)
        run = ParseRunRecord(
            parse_run_id=parse_run_id,
            document_id=document.document_id,
            content_sha256=document.content_sha256,
            parser_version=PARSER_VERSION,
            parsed=None,
            document_text=None,
            page_count=None,
            page_image_root=page_image_root,
            parse_error=None,
            status="RUNNING",
            requested_by=requested_by,
            job_run_id=None,
            started_at=datetime.now(UTC),
            completed_at=None,
        )

        try:
            await run_in_threadpool(
                self._documents.update_status,
                document_id,
                ELIGIBLE_DOCUMENT_STATES,
                "PARSING",
            )
        except InvalidDocumentStateError as error:
            raise DocumentServiceError(
                "DOCUMENT_NOT_PARSEABLE",
                f"Document cannot be parsed from status {error.document.status}.",
                409,
            ) from error

        await run_in_threadpool(self._parse_runs.create, run)
        return run, ParseJobRequest(
            parse_run_id=parse_run_id,
            document=document,
            page_image_root=page_image_root,
        )

    async def _submit(self, prepared: list[tuple[ParseRunRecord, ParseJobRequest]]) -> None:
        """Submit every prepared document as a single job run and record its identifier."""
        try:
            job_run_id = await run_in_threadpool(
                self._jobs.trigger, [request for _, request in prepared]
            )
            for run, _ in prepared:
                await run_in_threadpool(
                    self._parse_runs.assign_job_run, run.parse_run_id, job_run_id
                )
        except Exception as error:
            for run, _ in prepared:
                await self._roll_back(run.parse_run_id, run.document_id)
            raise DocumentServiceError(
                "PARSE_JOB_TRIGGER_FAILED",
                "The parsing job could not be started.",
                502,
                document_id=prepared[0][0].document_id if len(prepared) == 1 else None,
            ) from error

    async def _roll_back(self, parse_run_id: str, document_id: str) -> None:
        current_run = await run_in_threadpool(self._parse_runs.get, parse_run_id)
        if current_run and current_run.status == "RUNNING":
            await run_in_threadpool(
                self._parse_runs.fail,
                parse_run_id,
                {"error_message": "Parse job could not be started."},
            )
        current_document = await run_in_threadpool(self._documents.get, document_id)
        if current_document and current_document.status == "PARSING":
            await run_in_threadpool(
                self._documents.update_status, document_id, {"PARSING"}, "PARSE_FAILED"
            )

    async def get_run(self, parse_run_id: str) -> ParseRunRecord:
        run = await run_in_threadpool(self._parse_runs.get, parse_run_id)
        if run is None:
            raise DocumentServiceError("RUN_NOT_FOUND", "Parse run not found.", 404)
        if run.status == "RUNNING" and run.job_run_id is not None:
            poll = await run_in_threadpool(self._jobs.poll, run.job_run_id)
            if poll.state is ParseJobState.FAILED:
                await self._fail_running_job(run, poll.message)
                run = await run_in_threadpool(self._parse_runs.get, parse_run_id)
            elif poll.state is ParseJobState.SUCCEEDED:
                refreshed = await run_in_threadpool(self._parse_runs.get, parse_run_id)
                if refreshed and refreshed.status == "RUNNING":
                    await self._fail_running_job(
                        refreshed,
                        "Parse job completed without committing a terminal result.",
                    )
                    run = await run_in_threadpool(self._parse_runs.get, parse_run_id)
                else:
                    run = refreshed
        if run is None:
            raise RuntimeError("Parse run disappeared during polling")
        return run

    async def batch(self, job_run_id: int) -> list[ParseRunRecord]:
        """Every immutable run submitted under one job run, refreshed to a terminal state.

        A per-document task records its own outcome, so the job state only settles runs that
        never committed one.
        """
        runs = await run_in_threadpool(self._parse_runs.list_for_job_run, job_run_id)
        if not runs:
            raise DocumentServiceError("BATCH_NOT_FOUND", "Batch not found.", 404)
        if any(run.status == "RUNNING" for run in runs):
            poll = await run_in_threadpool(self._jobs.poll, job_run_id)
            if poll.state is not ParseJobState.RUNNING:
                for run in runs:
                    refreshed = await run_in_threadpool(self._parse_runs.get, run.parse_run_id)
                    if refreshed and refreshed.status == "RUNNING":
                        await self._fail_running_job(refreshed, poll.message)
                runs = await run_in_threadpool(self._parse_runs.list_for_job_run, job_run_id)
        return runs

    async def list_runs(self, document_id: str) -> list[ParseRunRecord]:
        document = await run_in_threadpool(self._documents.get, document_id)
        if document is None:
            raise DocumentServiceError("DOCUMENT_NOT_FOUND", "Document not found.", 404)
        return await run_in_threadpool(self._parse_runs.list_for_document, document_id)

    async def _fail_running_job(self, run: ParseRunRecord, message: str | None) -> None:
        await run_in_threadpool(
            self._parse_runs.fail,
            run.parse_run_id,
            {"error_message": (message or "Parse job failed.")[:500]},
        )
        document = await run_in_threadpool(self._documents.get, run.document_id)
        if document and document.status == "PARSING":
            await run_in_threadpool(
                self._documents.update_status,
                run.document_id,
                {"PARSING"},
                "PARSE_FAILED",
            )

    def _page_image_root(self, document_id: str, parse_run_id: str) -> str:
        if self._settings.mode is IdpMode.MOCK:
            return (
                self._settings.local_data_dir
                / "artifacts_volume"
                / "page_images"
                / document_id
                / parse_run_id
            ).as_posix()

        catalog = _required(self._settings.catalog, "IDP_CATALOG")
        project_schema = _required(self._settings.project_schema, "IDP_PROJECT_SCHEMA")
        artifacts_volume = _required(
            self._settings.artifacts_volume_name,
            "IDP_ARTIFACTS_VOLUME_NAME",
        )
        return (
            f"/Volumes/{catalog}/{project_schema}/{artifacts_volume}/page_images/"
            f"{document_id}/{parse_run_id}"
        )


def _required(value: str | None, name: str) -> str:
    if value is None:
        raise RuntimeError(f"Required trusted setting is absent: {name}")
    return value
