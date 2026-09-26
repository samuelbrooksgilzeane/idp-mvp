"""Owner-scoped, table-backed extraction progress. Reads never poll Jobs."""

import hashlib
import json
from dataclasses import replace
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from idp_app.api.dependencies import get_authenticated_user
from idp_app.core.config import IdpMode
from idp_app.services.documents import DocumentServiceError
from idp_app.services.extraction_batches import ExtractionBatch
from idp_app.services.extraction_queue import ExtractionQueue
from idp_app.services.extraction_queue_runtime import build_extraction_queue

router = APIRouter(prefix="/extraction-batches", tags=["extraction batches"])
User = Annotated[str, Depends(get_authenticated_user)]


def get_queue(request: Request) -> ExtractionQueue:
    if not request.app.state.settings.bulk_extraction_enabled:
        raise DocumentServiceError(
            "BULK_EXTRACTION_DISABLED", "Durable extraction is not enabled.", 409
        )
    existing = getattr(request.app.state, "extraction_queue", None)
    if isinstance(existing, ExtractionQueue):
        return existing
    queue = build_extraction_queue(request.app.state.settings)
    request.app.state.extraction_queue = queue
    return queue


Queue = Annotated[ExtractionQueue, Depends(get_queue)]


class CreateBatch(BaseModel):
    client_request_id: UUID
    document_ids: list[UUID] = Field(min_length=1, max_length=1000)
    schema_id: str = Field(min_length=1, max_length=128)
    schema_version: int = Field(ge=1)


class RetryBatch(BaseModel):
    client_request_id: UUID


def wake(request: Request) -> None:
    settings = request.app.state.settings
    if settings.mode is IdpMode.DATABRICKS and settings.dispatch_job_id:
        from idp_app.services.work_wakeup import wake_dispatcher

        wake_dispatcher(settings.dispatch_job_id)
    # Mock mode is drained by the explicit local worker, never by a status request.


def summary(queue: ExtractionQueue, batch: ExtractionBatch) -> dict[str, Any]:
    counts = queue.work.batch_counts(batch.batch_id)
    complete = batch.state == "ENQUEUED" and counts.get("SUCCEEDED", 0) + counts.get(
        "FAILED", 0
    ) == len(batch.document_ids)
    return {
        "batch_id": batch.batch_id,
        "state": "COMPLETE" if complete else batch.state,
        "total": len(batch.document_ids),
        "validated": len(batch.resolved),
        "counts": counts,
        "schema_id": batch.schema_id,
        "schema_version": batch.schema_version,
        "terminal": complete,
    }


@router.post("", status_code=202)
def create(body: CreateBatch, request: Request, queue: Queue, user: User) -> dict[str, Any]:
    batch = queue.batches.create(
        [str(identity) for identity in body.document_ids],
        body.schema_id,
        body.schema_version,
        user,
        str(body.client_request_id),
    )
    wake(request)
    return summary(queue, batch)


@router.get("/{batch_id}")
def status(batch_id: UUID, queue: Queue, user: User) -> dict[str, Any]:
    return summary(queue, queue.batches.owned(str(batch_id), user))


@router.get("/{batch_id}/items")
def items(
    batch_id: UUID,
    queue: Queue,
    user: User,
    after: Annotated[int, Query(ge=-1, le=999)] = -1,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    batch = queue.batches.owned(str(batch_id), user)
    stored = {item.ordinal: item for item in queue.work.batch_items(batch.batch_id, after, limit)}
    members = batch.resolved[after + 1 : after + 1 + limit]
    response = []
    for member in members:
        item = stored.get(member["ordinal"])
        response.append(
            {
                "document_id": member["document_id"],
                "document_name": member.get("file_name") or member["document_id"],
                "ordinal": member["ordinal"],
                "state": item.state if item else member["state"],
                "extraction_run_id": item.extraction_run_id
                if item
                else member["extraction_run_id"],
                "error_message": item.error_message if item else member.get("error_message"),
            }
        )
    cursor = (
        members[-1]["ordinal"]
        if members and members[-1]["ordinal"] + 1 < len(batch.resolved)
        else None
    )
    return {"items": response, "next_cursor": cursor}


@router.post("/{batch_id}/retry", status_code=202)
def retry(
    batch_id: UUID, body: RetryBatch, request: Request, queue: Queue, user: User
) -> dict[str, Any]:
    parent = queue.batches.owned(str(batch_id), user)
    request_id = str(body.client_request_id)
    identity = str(
        uuid5(NAMESPACE_URL, json.dumps([queue.batches.project, user, request_id, "EXTRACT"]))
    )
    digest = hashlib.sha256(f"retry:{parent.batch_id}".encode()).hexdigest()
    existing = queue.batches.batches.get(identity)
    if existing:
        result = queue.batches._replay(existing, user, digest)
    else:
        if not summary(queue, parent)["terminal"]:
            raise DocumentServiceError(
                "BATCH_STILL_ACTIVE", "Wait for this batch to finish before retrying.", 409
            )
        failed = [
            item
            for item in queue.work.batch_items(parent.batch_id, -1, 1000)
            if item.state == "FAILED"
        ]
        if not failed:
            raise DocumentServiceError(
                "NO_FAILED_MEMBERS", "This batch has no failed members.", 409
            )
        pins = {
            item.document_id: {
                "document_id": item.document_id,
                "parse_run_id": item.parse_run_id,
                "content_sha256": item.content_sha256,
            }
            for item in failed
            if item.parse_run_id and item.content_sha256
        }
        result = queue.batches.batches.create(
            replace(
                parent,
                batch_id=identity,
                client_request_id=request_id,
                manifest_hash=digest,
                document_ids=[item.document_id for item in failed],
                retry_inputs=pins,
                resolved=[],
                state="VALIDATING",
                published=0,
                revision=0,
                transition_id="initial",
            )
        )
        result = queue.batches._replay(result, user, digest)
    wake(request)
    return summary(queue, result)
