from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from idp_app.api.dependencies import get_export_service, get_reporting_service
from idp_app.api.models import ErrorResponse, ExportRequest, InvoiceSummaryResponse
from idp_app.services.export_service import ExportService
from idp_app.services.reporting import ReportingService

results_router = APIRouter(tags=["results"])


@results_router.post(
    "/exports",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def export_extraction_runs(
    body: ExportRequest,
    service: Annotated[ExportService, Depends(get_export_service)],
) -> StreamingResponse:
    """Generic, schema-driven export (section 7): a flat schema becomes one worksheet; every
    repeated collection becomes its own related sheet (or CSV, zipped); a singleton nested
    object flattens into dotted columns on its containing sheet. Replaces the invoice-only
    `/api/exports/invoices.xlsx` below for any schema.
    """
    if body.format == "csv":
        result = await service.export_csv_bundle(body.run_ids)
        media_type = "application/zip"
        filename = "extraction-results.zip"
    else:
        result = await service.export_workbook(body.run_ids)
        if result.is_multi_schema:
            media_type = "application/zip"
            filename = "extraction-results-by-schema.zip"
        else:
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            filename = "extraction-results.xlsx"
    return StreamingResponse(
        result.content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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
