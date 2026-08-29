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
        started_at = datetime.now(UTC)
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
            started_at=started_at,
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

        try:
            await run_in_threadpool(self._parse_runs.create, run)
            job_run_id = await run_in_threadpool(
                self._jobs.trigger,
                ParseJobRequest(
                    parse_run_id=parse_run_id,
                    document=document,
                    page_image_root=page_image_root,
                ),
            )
            await run_in_threadpool(
                self._parse_runs.assign_job_run,
                parse_run_id,
                job_run_id,
            )
        except Exception as error:
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
                    self._documents.update_status,
                    document_id,
                    {"PARSING"},
                    "PARSE_FAILED",
                )
            raise DocumentServiceError(
                "PARSE_JOB_TRIGGER_FAILED",
                "The parsing job could not be started.",
                502,
                document_id=document_id,
            ) from error

        created = await run_in_threadpool(self._parse_runs.get, parse_run_id)
        if created is None:
            raise RuntimeError("Created parse run could not be loaded")
        return created

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
