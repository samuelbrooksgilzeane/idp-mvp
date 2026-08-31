from typing import Annotated

from fastapi import APIRouter, Depends, Query

from idp_app.api.dependencies import (
    get_authenticated_user,
    get_extraction_results_service,
    get_extraction_service,
)
from idp_app.api.models import (
    ErrorResponse,
    ExtractedFieldResponse,
    ExtractionRequest,
    ExtractionResultResponse,
    ExtractionRunPageResponse,
    ExtractionRunResponse,
    ExtractionRunSummaryResponse,
    GenericExtractionRecordsResponse,
    GenericExtractionResponse,
    GenericFieldResultResponse,
    GenericRecordResponse,
    InvoiceCandidateResponse,
)
from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.extraction import ExtractionService
from idp_app.services.generic_results import ExtractionResultsService
from idp_app.services.schema_models import infer_root_mode

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
    run, fields, candidates = await service.latest(document_id)
    return ExtractionResultResponse(
        run=_run_response(run),
        fields=[ExtractedFieldResponse.model_validate(field) for field in fields],
        candidates=[
            InvoiceCandidateResponse.model_validate(candidate) for candidate in candidates
        ],
    )


@extraction_router.get(
    "/documents/{document_id}/extractions/{extraction_run_id}",
    response_model=ExtractionResultResponse,
    responses={404: {"model": ErrorResponse}},
)
async def extraction_result(
    document_id: str,
    extraction_run_id: str,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> ExtractionResultResponse:
    run, fields, candidates = await service.result(document_id, extraction_run_id)
    return ExtractionResultResponse(
        run=_run_response(run),
        fields=[ExtractedFieldResponse.model_validate(field) for field in fields],
        candidates=[
            InvoiceCandidateResponse.model_validate(candidate) for candidate in candidates
        ],
    )


@extraction_router.get(
    "/extractions",
    response_model=ExtractionRunPageResponse,
)
async def list_all_extraction_runs(
    service: Annotated[ExtractionResultsService, Depends(get_extraction_results_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    case_id: Annotated[str | None, Query(max_length=200)] = None,
    document_id: Annotated[str | None, Query(max_length=200)] = None,
    schema_id: Annotated[str | None, Query(max_length=100)] = None,
    status: Annotated[str | None, Query(max_length=20)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    latest_only: bool = True,
) -> ExtractionRunPageResponse:
    """A compact, cursor-paginated Results list, served by one joined repository query."""
    page = await service.list_page(
        limit=limit,
        cursor=cursor,
        case_id=_normalized(case_id),
        document_id=_normalized(document_id),
        schema_id=_normalized(schema_id),
        status=_normalized(status),
        search=_normalized(search),
        latest_only=latest_only,
    )
    return ExtractionRunPageResponse(
        items=[
        ExtractionRunSummaryResponse(
            extraction_run_id=item.extraction_run_id,
            document_id=item.document_id,
            document_name=item.document_name,
            case_id=item.case_id,
            schema_id=item.schema_id,
            schema_version=item.schema_version,
            schema_display_name=item.schema_display_name,
            status=item.status,  # type: ignore[arg-type]
            started_at=item.started_at,
            completed_at=item.completed_at,
            is_latest=item.is_latest,
        )
        for item in page.items
        ],
        next_cursor=page.next_cursor,
    )


@extraction_router.get(
    "/extractions/{extraction_run_id}",
    response_model=GenericExtractionResponse,
    responses={404: {"model": ErrorResponse}},
)
async def generic_extraction_result(
    extraction_run_id: str,
    service: Annotated[ExtractionResultsService, Depends(get_extraction_results_service)],
) -> GenericExtractionResponse:
    """Schema-agnostic replacement for `/documents/{id}/extractions/{run_id}` above: the
    hierarchical result exactly as `ai_extract` returned it, for any schema shape."""
    result = await service.get_result(extraction_run_id)
    return GenericExtractionResponse(
        run=_run_response(result.run),
        schema_id=result.schema.schema_id,
        schema_version=result.schema.schema_version,
        root_mode=infer_root_mode(result.schema.ai_extract_schema),
        result=result.hierarchy,
    )


@extraction_router.get(
    "/extractions/{extraction_run_id}/records",
    response_model=GenericExtractionRecordsResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def generic_extraction_records(
    extraction_run_id: str,
    service: Annotated[ExtractionResultsService, Depends(get_extraction_results_service)],
) -> GenericExtractionRecordsResponse:
    """The flat generic record/field tables (section 4's `extracted_records` and extended
    `extracted_fields`, computed from the retained raw result) behind a review grid or an
    export."""
    result = await service.get_records(extraction_run_id)
    return GenericExtractionRecordsResponse(
        run=_run_response(result.run),
        schema_id=result.schema.schema_id,
        schema_version=result.schema.schema_version,
        root_mode=infer_root_mode(result.schema.ai_extract_schema),
        records=[GenericRecordResponse.model_validate(record) for record in result.records],
        fields=[GenericFieldResultResponse.model_validate(field) for field in result.fields],
    )


def _run_response(run: ExtractionRunRecord) -> ExtractionRunResponse:
    return ExtractionRunResponse.model_validate(run)


def _normalized(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None
