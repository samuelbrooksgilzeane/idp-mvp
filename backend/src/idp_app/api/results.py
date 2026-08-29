from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from idp_app.api.dependencies import get_reporting_service
from idp_app.api.models import ErrorResponse, InvoiceSummaryResponse
from idp_app.services.reporting import ReportingService

results_router = APIRouter(tags=["results"])


@results_router.get(
    "/results/invoices",
    response_model=list[InvoiceSummaryResponse],
    responses={502: {"model": ErrorResponse}},
)
async def list_invoice_results(
    service: Annotated[ReportingService, Depends(get_reporting_service)],
    case_id: Annotated[str | None, Query(max_length=200)] = None,
) -> list[InvoiceSummaryResponse]:
    rows = await service.list_invoice_summaries(_normalized_case(case_id))
    return [InvoiceSummaryResponse.model_validate(row) for row in rows]


@results_router.get(
    "/exports/invoices.xlsx",
    responses={502: {"model": ErrorResponse}},
)
async def export_invoice_results(
    service: Annotated[ReportingService, Depends(get_reporting_service)],
    case_id: Annotated[str | None, Query(max_length=200)] = None,
) -> StreamingResponse:
    selected_case = _normalized_case(case_id)
    workbook = await service.export_invoice_workbook(selected_case)
    suffix = f"-{_safe_filename_part(selected_case)}" if selected_case else "-all-cases"
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="invoice-results{suffix}.xlsx"',
            "Cache-Control": "no-store",
        },
    )


def _normalized_case(case_id: str | None) -> str | None:
    normalized = case_id.strip() if case_id is not None else ""
    return normalized or None


def _safe_filename_part(value: str) -> str:
    safe = "".join(
        character
        if (character.isascii() and character.isalnum()) or character in "-_"
        else "-"
        for character in value
    )
    return safe[:80] or "case"
