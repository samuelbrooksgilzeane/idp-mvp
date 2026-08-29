from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile

from idp_app.api.dependencies import get_authenticated_user, get_document_service
from idp_app.api.models import (
    DocumentResponse,
    ErrorResponse,
    UploadBatchResponse,
    UploadFailure,
)
from idp_app.core.config import Settings
from idp_app.services.document_models import UploadMetadata
from idp_app.services.documents import DocumentService, DocumentServiceError

documents_router = APIRouter(prefix="/documents", tags=["documents"])


@documents_router.post(
    "",
    response_model=UploadBatchResponse,
    status_code=201,
    responses={
        207: {"model": UploadBatchResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        415: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def upload_documents(
    request: Request,
    response: Response,
    files: Annotated[list[UploadFile], File(description="One or more PDF files")],
    service: Annotated[DocumentService, Depends(get_document_service)],
    uploaded_by: Annotated[str, Depends(get_authenticated_user)],
    case_id: Annotated[str | None, Form(max_length=200)] = None,
    template_id: Annotated[str, Form(max_length=100)] = "invoice_v1",
    use_case: Annotated[str, Form(max_length=100)] = "invoice",
) -> UploadBatchResponse:
    settings: Settings = request.app.state.settings
    if not files:
        raise DocumentServiceError("UPLOAD_EMPTY", "At least one PDF is required.", 422)
    if len(files) > settings.max_upload_files:
        raise DocumentServiceError(
            "TOO_MANY_FILES",
            f"At most {settings.max_upload_files} PDFs can be uploaded at once.",
            422,
        )

    metadata = UploadMetadata(
        case_id=case_id.strip() if case_id and case_id.strip() else None,
        template_id=template_id.strip(),
        use_case=use_case.strip(),
    )
    documents: list[DocumentResponse] = []
    errors: list[UploadFailure] = []
    for upload in files:
        try:
            document = await service.upload(upload, metadata, uploaded_by)
            documents.append(DocumentResponse.model_validate(document))
        except DocumentServiceError as error:
            if len(files) == 1:
                raise
            errors.append(
                UploadFailure(
                    file_name=upload.filename or "unnamed",
                    code=error.code,
                    message=error.message,
                    document_id=error.document_id,
                )
            )

    if errors:
        response.status_code = 207
    return UploadBatchResponse(documents=documents, errors=errors)


@documents_router.get("", response_model=list[DocumentResponse])
async def list_documents(
    service: Annotated[DocumentService, Depends(get_document_service)],
    case_id: Annotated[str | None, Query(max_length=200)] = None,
) -> list[DocumentResponse]:
    selected_case = case_id.strip() if case_id and case_id.strip() else None
    documents = await service.list_documents(selected_case)
    return [DocumentResponse.model_validate(document) for document in documents]


@documents_router.get("/cases", response_model=list[str])
async def list_document_cases(
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[str]:
    return await service.list_case_ids()


@documents_router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}},
)
async def get_document(
    document_id: str,
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentResponse:
    document = await service.get_document(document_id)
    return DocumentResponse.model_validate(document)
