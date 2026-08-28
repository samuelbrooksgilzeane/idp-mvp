from typing import Annotated

from fastapi import APIRouter, Depends

from idp_app.api.dependencies import get_authenticated_user, get_parsing_service
from idp_app.api.models import ErrorResponse, ParseRunResponse
from idp_app.services.document_models import ParseRunRecord
from idp_app.services.parsing import ParsingService

parsing_router = APIRouter(tags=["parsing"])


@parsing_router.post(
    "/documents/{document_id}/parse",
    response_model=ParseRunResponse,
    status_code=202,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def parse_document(
    document_id: str,
    service: Annotated[ParsingService, Depends(get_parsing_service)],
    requested_by: Annotated[str, Depends(get_authenticated_user)],
) -> ParseRunResponse:
    return _response(await service.start(document_id, requested_by))


@parsing_router.get(
    "/documents/{document_id}/parse-runs",
    response_model=list[ParseRunResponse],
    responses={404: {"model": ErrorResponse}},
)
async def list_parse_runs(
    document_id: str,
    service: Annotated[ParsingService, Depends(get_parsing_service)],
) -> list[ParseRunResponse]:
    return [_response(run) for run in await service.list_runs(document_id)]


@parsing_router.get(
    "/runs/{parse_run_id}",
    response_model=ParseRunResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_parse_run(
    parse_run_id: str,
    service: Annotated[ParsingService, Depends(get_parsing_service)],
) -> ParseRunResponse:
    return _response(await service.get_run(parse_run_id))


def _response(run: ParseRunRecord) -> ParseRunResponse:
    return ParseRunResponse.model_validate(run, from_attributes=True)
