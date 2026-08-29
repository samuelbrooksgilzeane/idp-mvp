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


class BoundingBoxResponse(BaseModel):
    page_id: int
    x: float
    y: float
    width: float
    height: float


class ElementResponse(BaseModel):
    element_id: int
    element_type: str
    content: str
    confidence: float | None
    description: str | None
    boxes: list[BoundingBoxResponse]


class PageResponse(BaseModel):
    page_id: int
    page_number: int
    element_count: int
    element_types: list[str]
    image_url: str


class SchemaSummaryResponse(BaseModel):
    schema_id: str
    schema_version: int
    display_name: str
    use_case: str
    schema_hash: str
    status: Literal["PRODUCTION"]


class SchemaFieldResponse(BaseModel):
    field_path: str
    label: str
    field_type: str
    description: str
    required: bool
    citation_required: bool
    confidence_threshold: float
    risk_tier: Literal["low", "medium", "high"]


class SchemaRuleResponse(BaseModel):
    rule_id: str
    rule_type: str
    description: str
    field_paths: list[str]
    tolerance: float | None


class SchemaDetailResponse(SchemaSummaryResponse):
    instructions: str
    fields: list[SchemaFieldResponse]
    document_rules: list[SchemaRuleResponse]
