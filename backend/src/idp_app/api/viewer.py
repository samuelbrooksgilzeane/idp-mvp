from collections.abc import Iterator
from typing import Annotated, BinaryIO
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from idp_app.api.dependencies import get_viewer_service
from idp_app.api.models import BoundingBoxResponse, ElementResponse, ErrorResponse, PageResponse
from idp_app.services.viewer import ParsedElement, ParsedPage, ViewerService

viewer_router = APIRouter(tags=["viewer"])


@viewer_router.get(
    "/documents/{document_id}/pages",
    response_model=list[PageResponse],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def list_pages(
    document_id: str,
    service: Annotated[ViewerService, Depends(get_viewer_service)],
    parse_run_id: Annotated[str | None, Query(max_length=100)] = None,
) -> list[PageResponse]:
    return [
        _page_response(document_id, page, parse_run_id)
        for page in await service.list_pages(document_id, parse_run_id)
    ]


@viewer_router.get(
    "/documents/{document_id}/pages/{page_id}/image",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    response_class=StreamingResponse,
)
async def get_page_image(
    document_id: str,
    page_id: int,
    service: Annotated[ViewerService, Depends(get_viewer_service)],
    parse_run_id: Annotated[str | None, Query(max_length=100)] = None,
) -> StreamingResponse:
    image = await service.open_page_image(document_id, page_id, parse_run_id)
    headers = {
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }
    if image.content_length is not None:
        headers["Content-Length"] = str(image.content_length)
    return StreamingResponse(
        _stream(image.contents),
        media_type=image.media_type,
        headers=headers,
    )


@viewer_router.get(
    "/documents/{document_id}/elements",
    response_model=list[ElementResponse],
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def list_elements(
    document_id: str,
    service: Annotated[ViewerService, Depends(get_viewer_service)],
    page_id: Annotated[int, Query(ge=0)],
    element_type: Annotated[str | None, Query(alias="type", max_length=80)] = None,
    parse_run_id: Annotated[str | None, Query(max_length=100)] = None,
) -> list[ElementResponse]:
    return [
        _element_response(element)
        for element in await service.list_elements(
            document_id, page_id, element_type, parse_run_id
        )
    ]


def _page_response(
    document_id: str, page: ParsedPage, parse_run_id: str | None
) -> PageResponse:
    image_url = f"/api/documents/{document_id}/pages/{page.page_id}/image"
    if parse_run_id is not None:
        image_url += "?" + urlencode({"parse_run_id": parse_run_id})
    return PageResponse(
        page_id=page.page_id,
        page_number=page.page_number,
        element_count=page.element_count,
        element_types=page.element_types,
        image_url=image_url,
    )


def _element_response(element: ParsedElement) -> ElementResponse:
    return ElementResponse(
        element_id=element.element_id,
        element_type=element.element_type,
        content=element.content,
        confidence=element.confidence,
        description=element.description,
        boxes=[
            BoundingBoxResponse.model_validate(box, from_attributes=True)
            for box in element.boxes
        ],
    )


def _stream(contents: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := contents.read(1024 * 1024):
            yield chunk
    finally:
        contents.close()
