"""Batch submission and progress.

The API exposes documents and a batch identity only. How a batch executes is a deployment
concern, so the engine can change without altering this contract.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from idp_app.api.dependencies import (
    get_authenticated_user,
    get_extraction_service,
    get_parsing_service,
)
from idp_app.api.models import (
    BatchFailureResponse,
    BatchMemberResponse,
    BatchResponse,
    BatchStatusResponse,
    ErrorResponse,
    ExtractionBatchRequest,
    ParseBatchRequest,
)
from idp_app.services.extraction import ExtractionService
from idp_app.services.parsing import ParsingService

batches_router = APIRouter(tags=["batches"], prefix="/batches")

SUCCEEDED = {"SUCCESS", "EXTRACTED"}


@batches_router.post(
    "/parse",
    response_model=BatchResponse,
    status_code=202,
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def parse_batch(
    body: ParseBatchRequest,
    service: Annotated[ParsingService, Depends(get_parsing_service)],
    requested_by: Annotated[str, Depends(get_authenticated_user)],
) -> BatchResponse:
    runs, failures = await service.start_batch(body.document_ids, requested_by)
    return BatchResponse(
        kind="parse",
        job_run_id=next((run.job_run_id for run in runs if run.job_run_id is not None), None),
        requested=len(set(body.document_ids)),
        accepted=len(runs),
        members=[
            BatchMemberResponse(
                document_id=run.document_id, run_id=run.parse_run_id, status=run.status
            )
            for run in runs
        ],
        errors=[BatchFailureResponse.model_validate(failure) for failure in failures],
    )


@batches_router.post(
    "/extract",
    response_model=BatchResponse,
    status_code=202,
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def extract_batch(
    body: ExtractionBatchRequest,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
    requested_by: Annotated[str, Depends(get_authenticated_user)],
) -> BatchResponse:
    runs, failures = await service.start_batch(
        body.document_ids, body.schema_id, body.schema_version, requested_by
    )
    return BatchResponse(
        kind="extract",
        job_run_id=next((run.job_run_id for run in runs if run.job_run_id is not None), None),
        requested=len(set(body.document_ids)),
        accepted=len(runs),
        members=[
            BatchMemberResponse(
                document_id=run.document_id, run_id=run.extraction_run_id, status=run.status
            )
            for run in runs
        ],
        errors=[BatchFailureResponse.model_validate(failure) for failure in failures],
    )


@batches_router.get(
    "/parse/{job_run_id}",
    response_model=BatchStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def parse_batch_status(
    job_run_id: int,
    service: Annotated[ParsingService, Depends(get_parsing_service)],
) -> BatchStatusResponse:
    runs = await service.batch(job_run_id)
    return _status(
        "parse",
        job_run_id,
        [(run.document_id, run.parse_run_id, run.status) for run in runs],
    )


@batches_router.get(
    "/extract/{job_run_id}",
    response_model=BatchStatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def extract_batch_status(
    job_run_id: int,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> BatchStatusResponse:
    runs = await service.batch(job_run_id)
    return _status(
        "extract",
        job_run_id,
        [(run.document_id, run.extraction_run_id, run.status) for run in runs],
    )


def _status(
    kind: str, job_run_id: int, members: list[tuple[str, str, str]]
) -> BatchStatusResponse:
    statuses = [status for _, _, status in members]
    return BatchStatusResponse(
        kind=kind,  # type: ignore[arg-type]
        job_run_id=job_run_id,
        total=len(members),
        running=sum(1 for status in statuses if status == "RUNNING"),
        succeeded=sum(1 for status in statuses if status in SUCCEEDED),
        failed=sum(1 for status in statuses if status == "FAILED"),
        members=[
            BatchMemberResponse(document_id=document_id, run_id=run_id, status=status)
            for document_id, run_id, status in members
        ],
    )
