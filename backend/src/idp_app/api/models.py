from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from idp_app.core.config import IdpMode


class ConfigurationPresence(BaseModel):
    catalog: bool
    project_schema: bool
    table_prefix: bool
    source_volume_name: bool
    artifacts_volume_name: bool
    warehouse_id: bool
    parse_job_id: bool
    extraction_job_id: bool
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
    status: Literal[
        "UPLOADED",
        "PARSING",
        "PARSED",
        "PARSE_FAILED",
        "EXTRACTING",
        "EXTRACTED",
        "EXTRACT_FAILED",
        "VALIDATING",
        "VALIDATED_PASS",
        "REVIEW_REQUIRED",
    ]
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
    schema_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    schema_version: int = Field(ge=1)
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


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    schema_version: int = Field(ge=1)


class ExtractionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    extraction_run_id: str
    document_id: str
    parse_run_id: str
    schema_id: str
    schema_version: int
    schema_hash: str
    extractor_version: Literal["2.1"]
    options: dict[str, str]
    error_message: str | None
    status: Literal["RUNNING", "EXTRACTED", "FAILED"]
    requested_by: str
    job_run_id: int | None
    started_at: datetime
    completed_at: datetime | None


class ExtractedFieldResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    field_path: str
    field_type: str
    value: Any
    value_string: str | None
    confidence_score: float | None
    citation_ids: list[int]
    citations: list[dict[str, Any]]
    extraction_error: str | None


class InvoiceCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: str | None
    document_id: str
    template_id: str
    invoice_number: str | None
    invoice_date: date | None
    seller_name: str | None
    subtotal: Decimal | None
    discount_amount: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    currency: str | None
    extraction_run_id: str
    schema_version: int


class ExtractionResultResponse(BaseModel):
    run: ExtractionRunResponse
    fields: list[ExtractedFieldResponse]
    candidate: InvoiceCandidateResponse | None


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extraction_run_id: str | None = None


class ValidationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    field_path: str | None
    validator_type: str
    severity: Literal["INFO", "WARNING", "BLOCKING"]
    status: Literal["PASS", "FAIL", "UNCERTAIN", "SKIPPED"]
    message: str
    actual_value: str | None
    expected_value: str | None
    evidence: str | None
    validator_version: str


class ValidationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    validation_run_id: str
    document_id: str
    extraction_run_id: str
    schema_id: str
    schema_version: int
    schema_hash: str
    validator_version: str
    status: str
    document_status: Literal["VALIDATED_PASS", "REVIEW_REQUIRED"]
    requested_by: str
    started_at: datetime
    completed_at: datetime | None


class ValidationSummaryResponse(BaseModel):
    total: int
    passed: int
    failed: int
    uncertain: int
    skipped: int
    blocking: int
    warning: int
    info: int


class ValidationReportResponse(BaseModel):
    run: ValidationRunResponse
    summary: ValidationSummaryResponse
    results: list[ValidationResultResponse]
