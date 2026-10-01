from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from idp_app.api.dependencies import get_export_service
from idp_app.api.models import ErrorResponse, ExportRequest
from idp_app.services.export_service import ExportService

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
    object flattens into dotted columns on its containing sheet.
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

