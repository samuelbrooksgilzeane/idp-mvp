from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from idp_app.core.config import IdpMode


class ConfigurationPresence(BaseModel):
    catalog: bool
    project_schema: bool
    table_prefix: bool
    source_volume_name: bool
    artifacts_volume_name: bool
    warehouse_id: bool
    parse_job_id: bool
    validation_endpoint: bool


class HealthResponse(BaseModel):
    status: Literal["ok"]
    mode: IdpMode
    application_name: str
    configuration: ConfigurationPresence


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    case_id: str | None
    template_id: str
    use_case: str
    file_name: str
    file_size: int
    content_sha256: str
    status: Literal["UPLOADED", "PARSING", "PARSED", "PARSE_FAILED"]
    uploaded_by: str
    uploaded_at: datetime
    updated_at: datetime


class UploadFailure(BaseModel):
    file_name: str
    code: str
    message: str
    document_id: str | None = None


class UploadBatchResponse(BaseModel):
    documents: list[DocumentResponse]
    errors: list[UploadFailure]


class ErrorDetail(BaseModel):
    code: str
    message: str
    document_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ParseRunResponse(BaseModel):
    parse_run_id: str
    document_id: str
    parser_version: Literal["2.0"]
    status: Literal["RUNNING", "SUCCESS", "FAILED"]
    page_count: int | None
    parse_error: dict[str, object] | list[object] | None
    requested_by: str
    started_at: datetime
    completed_at: datetime | None
