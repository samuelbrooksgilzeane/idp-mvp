from typing import Annotated

from fastapi import APIRouter, Depends, Query

from idp_app.api.dependencies import get_authenticated_user, get_upload_batch_service
from idp_app.api.models import (
    CreatedUploadBatch,
    CreateUploadBatchRequest,
    UploadBatchSummary,
    UploadItemPage,
)
from idp_app.services.upload_batches import UploadBatchService

upload_batches_router = APIRouter(prefix="/upload-batches", tags=["upload batches"])
Service = Annotated[UploadBatchService, Depends(get_upload_batch_service)]
User = Annotated[str, Depends(get_authenticated_user)]


@upload_batches_router.post("", response_model=CreatedUploadBatch, status_code=201)
def create_upload_batch(
    body: CreateUploadBatchRequest, service: Service, user: User
) -> CreatedUploadBatch:
    result = service.create(
        user,
        body.client_request_id,
        (body.case_id or "").strip() or None,
        [item.model_dump() for item in body.files],
    )
    return CreatedUploadBatch.model_validate(result)


@upload_batches_router.get("/{batch_id}", response_model=UploadBatchSummary)
def get_upload_batch(batch_id: str, service: Service, user: User) -> UploadBatchSummary:
    return UploadBatchSummary.model_validate(service.summary(batch_id, user))


@upload_batches_router.get("/{batch_id}/items", response_model=UploadItemPage)
def get_upload_items(
    batch_id: str,
    service: Service,
    user: User,
    cursor: Annotated[int, Query(ge=-1, le=999)] = -1,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> UploadItemPage:
    return UploadItemPage.model_validate(service.page(batch_id, user, cursor, limit))
