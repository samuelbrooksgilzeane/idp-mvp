from typing import Annotated

from fastapi import APIRouter, Depends

from idp_app.api.dependencies import get_authenticated_user, get_extraction_service
from idp_app.api.models import (
    ErrorResponse,
    ExtractedFieldResponse,
    ExtractionRequest,
    ExtractionResultResponse,
    ExtractionRunResponse,
    InvoiceCandidateResponse,
)
from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.extraction import ExtractionService

extraction_router = APIRouter(tags=["extraction"])


@extraction_router.post(
    "/documents/{document_id}/extract",
    response_model=ExtractionRunResponse,
    status_code=202,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def extract_document(
    document_id: str,
    body: ExtractionRequest,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
    requested_by: Annotated[str, Depends(get_authenticated_user)],
) -> ExtractionRunResponse:
    run = await service.start(
        document_id, body.schema_id, body.schema_version, requested_by
    )
    return _run_response(run)


@extraction_router.get(
    "/documents/{document_id}/extraction-runs",
    response_model=list[ExtractionRunResponse],
    responses={404: {"model": ErrorResponse}},
)
async def list_extraction_runs(
    document_id: str,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> list[ExtractionRunResponse]:
    return [_run_response(run) for run in await service.list_runs(document_id)]


@extraction_router.get(
    "/documents/{document_id}/extractions/latest",
    response_model=ExtractionResultResponse,
    responses={404: {"model": ErrorResponse}},
)
async def latest_extraction(
    document_id: str,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> ExtractionResultResponse:
    run, fields, candidate = await service.latest(document_id)
    return ExtractionResultResponse(
        run=_run_response(run),
        fields=[ExtractedFieldResponse.model_validate(field) for field in fields],
        candidate=(
            InvoiceCandidateResponse.model_validate(candidate) if candidate is not None else None
        ),
    )


def _run_response(run: ExtractionRunRecord) -> ExtractionRunResponse:
    return ExtractionRunResponse.model_validate(run)
