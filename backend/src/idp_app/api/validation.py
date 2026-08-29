from typing import Annotated

from fastapi import APIRouter, Depends

from idp_app.api.dependencies import get_authenticated_user, get_validation_service
from idp_app.api.models import (
    ErrorResponse,
    ValidationReportResponse,
    ValidationRequest,
    ValidationResultResponse,
    ValidationRunResponse,
    ValidationSummaryResponse,
)
from idp_app.services.document_models import ValidationResultRecord, ValidationRunRecord
from idp_app.services.validation_service import ValidationService, summarise

validation_router = APIRouter(tags=["validation"])


@validation_router.post(
    "/documents/{document_id}/validate",
    response_model=ValidationReportResponse,
    status_code=201,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def validate_document(
    document_id: str,
    body: ValidationRequest,
    service: Annotated[ValidationService, Depends(get_validation_service)],
    requested_by: Annotated[str, Depends(get_authenticated_user)],
) -> ValidationReportResponse:
    run, results = await service.validate(document_id, requested_by, body.extraction_run_id)
    return _report(run, results)


@validation_router.get(
    "/documents/{document_id}/validation-runs",
    response_model=list[ValidationRunResponse],
    responses={404: {"model": ErrorResponse}},
)
async def list_validation_runs(
    document_id: str,
    service: Annotated[ValidationService, Depends(get_validation_service)],
) -> list[ValidationRunResponse]:
    runs = await service.list_runs(document_id)
    return [ValidationRunResponse.model_validate(run) for run in runs]


@validation_router.get(
    "/documents/{document_id}/validations/latest",
    response_model=ValidationReportResponse,
    responses={404: {"model": ErrorResponse}},
)
async def latest_validation(
    document_id: str,
    service: Annotated[ValidationService, Depends(get_validation_service)],
) -> ValidationReportResponse:
    run, results = await service.latest(document_id)
    return _report(run, results)


@validation_router.get(
    "/documents/{document_id}/validation-summary",
    response_model=ValidationSummaryResponse,
    responses={404: {"model": ErrorResponse}},
)
async def validation_summary(
    document_id: str,
    service: Annotated[ValidationService, Depends(get_validation_service)],
) -> ValidationSummaryResponse:
    _, results = await service.latest(document_id)
    return ValidationSummaryResponse(**summarise(results))


@validation_router.get(
    "/documents/{document_id}/validations/{validation_run_id}",
    response_model=ValidationReportResponse,
    responses={404: {"model": ErrorResponse}},
)
async def validation_result(
    document_id: str,
    validation_run_id: str,
    service: Annotated[ValidationService, Depends(get_validation_service)],
) -> ValidationReportResponse:
    run, results = await service.result(document_id, validation_run_id)
    return _report(run, results)


def _report(
    run: ValidationRunRecord, results: list[ValidationResultRecord]
) -> ValidationReportResponse:
    return ValidationReportResponse(
        run=ValidationRunResponse.model_validate(run),
        summary=ValidationSummaryResponse(**summarise(results)),
        results=[ValidationResultResponse.model_validate(item) for item in results],
    )
