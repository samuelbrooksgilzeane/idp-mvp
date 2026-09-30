from typing import Annotated

from fastapi import APIRouter, Depends

from idp_app.api.dependencies import get_authenticated_user, get_folder_import_service
from idp_app.api.models import (
    ImportFolderList,
    StartedFolderImport,
    StartFolderImportRequest,
    UploadBatchSummary,
)
from idp_app.services.folder_import import FolderImportService

# Progress is read through the upload-batch endpoints; a folder import is an upload batch.
imports_router = APIRouter(prefix="/imports", tags=["folder import"])
Service = Annotated[FolderImportService, Depends(get_folder_import_service)]
User = Annotated[str, Depends(get_authenticated_user)]


@imports_router.get("/folders", response_model=ImportFolderList)
def list_import_folders(service: Service, user: User) -> ImportFolderList:
    del user
    return ImportFolderList.model_validate(service.folders())


@imports_router.post("", response_model=StartedFolderImport, status_code=201)
def start_folder_import(
    body: StartFolderImportRequest, service: Service, user: User
) -> StartedFolderImport:
    return StartedFolderImport.model_validate(
        service.start(
            user, body.client_request_id, body.folder, (body.case_id or "").strip() or None
        )
    )


@imports_router.post("/{batch_id}/resume", response_model=UploadBatchSummary)
def resume_folder_import(batch_id: str, service: Service, user: User) -> UploadBatchSummary:
    return UploadBatchSummary.model_validate(service.resume(batch_id, user))
