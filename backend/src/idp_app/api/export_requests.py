import hashlib
from collections.abc import Iterator
from typing import Annotated, Any, Literal, Self, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from idp_app.api.dependencies import get_authenticated_user
from idp_app.core.config import IdpMode
from idp_app.services.documents import DocumentServiceError
from idp_app.services.export_artifacts import ExportArtifacts
from idp_app.services.export_jobs import build_exports
from idp_app.services.export_requests import ExportRequests
from idp_app.services.export_service import ExportService

router = APIRouter(prefix="/export-requests", tags=["exports"])
User = Annotated[str, Depends(get_authenticated_user)]


class CreateExport(BaseModel):
    client_request_id: UUID
    format: Literal["xlsx", "csv"]
    run_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=1000)
    extraction_batch_id: UUID | None = None
    completed_only: bool = False
    include_historical_duplicates: bool = False

    @model_validator(mode="after")
    def selection(self) -> Self:
        if (self.run_ids is None) == (self.extraction_batch_id is None):
            raise ValueError("Choose run_ids or extraction_batch_id")
        return self


def services(request: Request) -> tuple[ExportRequests, ExportService, ExportArtifacts]:
    if not request.app.state.settings.bulk_export_enabled:
        raise DocumentServiceError("BULK_EXPORT_DISABLED", "Durable exports are not enabled.", 409)
    if not hasattr(request.app.state, "durable_exports"):
        request.app.state.durable_exports = build_exports(request.app.state.settings)
    return cast(
        tuple[ExportRequests, ExportService, ExportArtifacts], request.app.state.durable_exports
    )


def response(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: row.get(key)
        for key in (
            "export_id",
            "state",
            "selected_count",
            "runs_processed",
            "bytes",
            "filename",
            "error",
            "expires_at",
        )
    }
    result["download_url"] = (
        f"/api/export-requests/{row['export_id']}/download" if row["state"] == "SUCCEEDED" else None
    )
    return result


@router.post("", status_code=202, response_model=None)
def create(body: CreateExport, request: Request, user: User) -> dict[str, Any] | JSONResponse:
    requests, sources, _ = services(request)
    request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    existing = requests.get(requests.identity(user, str(body.client_request_id)))
    if existing is not None:
        if existing.get("request_hash") != request_hash:
            raise DocumentServiceError(
                "EXPORT_REQUEST_CONFLICT", "Request ID already used with different inputs.", 409
            )
        return submit(requests.owned(existing["export_id"], user), request)
    run_ids = list(dict.fromkeys(str(identity) for identity in body.run_ids or []))
    if body.extraction_batch_id:
        from idp_app.api.extraction_batches import get_queue

        queue = get_queue(request)
        batch = queue.batches.owned(str(body.extraction_batch_id), user)
        items = queue.work.batch_items(batch.batch_id, -1, 1000)
        eligible = [
            item.extraction_run_id
            for item in items
            if item.state == "SUCCEEDED" and item.extraction_run_id is not None
        ]
        failed = sum(item.state == "FAILED" for item in items)
        pending = len(batch.document_ids) - len(eligible) - failed
        if (pending or failed) and not body.completed_only:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "CONFIRM_COMPLETED_ONLY",
                    "eligible": len(eligible),
                    "pending": pending,
                    "failed": failed,
                    "message": "Confirm export of completed results only.",
                },
            )
        run_ids = eligible
    # Validate selected metadata in bounded joins. Never resolve a newer run in the worker.
    document_schemas: set[tuple[str, str]] = set()
    duplicates = False
    for offset in range(0, len(run_ids), 25):
        page = list(dict.fromkeys(run_ids[offset : offset + 25]))
        selected = sources._sources.get_many(page)
        if len(selected) != len(page) or any(
            s.run.status != "EXTRACTED"
            or s.run.ai_result is None
            or s.run.schema_hash != s.schema.schema_hash
            for s in selected
        ):
            raise DocumentServiceError(
                "EXPORT_NOT_READY", "All selected results must be available and successful.", 409
            )
        for source in selected:
            key = (source.run.document_id, source.run.schema_id)
            duplicates = duplicates or key in document_schemas
            document_schemas.add(key)
    if duplicates and not body.include_historical_duplicates:
        return JSONResponse(
            status_code=409,
            content={
                "code": "CONFIRM_HISTORICAL_DUPLICATES",
                "message": (
                    "Selection contains multiple runs for the same document and template. "
                    "Confirm including all selected historical runs."
                ),
            },
        )
    row = requests.create(
        user,
        str(body.client_request_id),
        body.format,
        run_ids,
        request.app.state.settings.export_retention_hours,
        request_hash=request_hash,
    )
    return submit(row, request)


def submit(row: dict[str, Any], request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if settings.mode is IdpMode.DATABRICKS and row["state"] == "QUEUED":
        from databricks.sdk import WorkspaceClient

        try:
            WorkspaceClient().jobs.run_now(
                job_id=settings.export_job_id,
                idempotency_token=f"export-{row['export_id']}",
                job_parameters={"export_id": row["export_id"]},
            )
        except Exception as error:
            # Leave durable QUEUED intent for an explicit replay, preserving the stable token.
            raise DocumentServiceError(
                "EXPORT_SUBMISSION_PENDING",
                "Export saved. Retry this request to confirm Job submission.",
                503,
            ) from error
    return response(row)


@router.get("/{export_id}")
def status(export_id: UUID, request: Request, user: User) -> dict[str, Any]:
    requests, _, _ = services(request)
    return response(requests.owned(str(export_id), user))


@router.get("/{export_id}/download")
def download(export_id: UUID, request: Request, user: User) -> StreamingResponse:
    requests, _, artifacts = services(request)
    row = requests.owned(str(export_id), user)
    if row["state"] != "SUCCEEDED":
        raise DocumentServiceError(
            "EXPORT_NOT_READY",
            "Export is not ready or has expired.",
            410 if row["state"] == "EXPIRED" else 409,
        )
    try:
        stream = artifacts.open(str(export_id))
    except FileNotFoundError as error:
        raise DocumentServiceError(
            "EXPORT_ARTIFACT_MISSING", "Artifact unavailable; create another export.", 410
        ) from error

    def chunks() -> Iterator[bytes]:
        try:
            yield from iter(lambda: stream.read(1024 * 1024), b"")
        finally:
            stream.close()

    return StreamingResponse(
        chunks(),
        media_type=row["media_type"],
        headers={
            "Content-Disposition": f'attachment; filename="{row["filename"]}"',
            "Content-Length": str(row["bytes"]),
            "Cache-Control": "private, no-store",
        },
    )
