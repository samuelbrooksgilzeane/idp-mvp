from typing import Annotated

from fastapi import APIRouter, Depends

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
    response_model=list[ExtractionRunSummaryResponse],
)
async def list_all_extraction_runs(
    service: Annotated[ExtractionResultsService, Depends(get_extraction_results_service)],
    case_id: str | None = None,
    document_id: str | None = None,
    schema_id: str | None = None,
    status: str | None = None,
) -> list[ExtractionRunSummaryResponse]:
    """The run-centric Results list: every extraction run across every document, joined with
    its document and schema context. Replaces the invoice-only `/api/results/invoices` for the
    Results page (any schema shape, not just invoices)."""
    summaries = await service.list_summaries(
        case_id=case_id, document_id=document_id, schema_id=schema_id, status=status
    )
    return [
        ExtractionRunSummaryResponse(
            extraction_run_id=summary.run.extraction_run_id,
            document_id=summary.run.document_id,
            document_name=summary.document_name,
            case_id=summary.case_id,
            schema_id=summary.run.schema_id,
            schema_version=summary.run.schema_version,
            schema_display_name=summary.schema_display_name,
            status=summary.run.status,  # type: ignore[arg-type]
            started_at=summary.run.started_at,
            completed_at=summary.run.completed_at,
            is_latest=summary.is_latest,
            records_count=summary.records_count,
            issues_count=summary.issues_count,
        )
        for summary in summaries
    ]


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
