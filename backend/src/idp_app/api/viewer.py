from collections.abc import Iterator
from typing import Annotated, BinaryIO

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
) -> list[PageResponse]:
    return [_page_response(document_id, page) for page in await service.list_pages(document_id)]


@viewer_router.get(
    "/documents/{document_id}/pages/{page_id}/image",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    response_class=StreamingResponse,
)
async def get_page_image(
    document_id: str,
    page_id: int,
    service: Annotated[ViewerService, Depends(get_viewer_service)],
) -> StreamingResponse:
    image = await service.open_page_image(document_id, page_id)
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
) -> list[ElementResponse]:
    return [
        _element_response(element)
        for element in await service.list_elements(document_id, page_id, element_type)
    ]


def _page_response(document_id: str, page: ParsedPage) -> PageResponse:
    return PageResponse(
        page_id=page.page_id,
        page_number=page.page_number,
        element_count=page.element_count,
        element_types=page.element_types,
        image_url=f"/api/documents/{document_id}/pages/{page.page_id}/image",
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
