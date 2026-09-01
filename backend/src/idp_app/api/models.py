from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from idp_app.core.config import IdpMode
from idp_app.services.schema_models import ExtractField


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
    description: str | None = None
    use_case: str
    schema_hash: str
    status: Literal["PRODUCTION", "DRAFT", "PUBLISHED", "RETIRED"]
    root_mode: Literal["SINGLE_RECORD", "REPEATED_RECORDS"]
    is_editable: bool
    created_by: str
    created_at: datetime
    published_at: datetime | None = None


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
    # The full recursive tree exactly as declared, for the schema editor (`fields` above stays
    # a flattened, wildcard-keyed leaf list for read-only display and citation lookup).
    schema_tree: dict[str, ExtractField]


class CreateSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    root_mode: Literal["SINGLE_RECORD", "REPEATED_RECORDS"]
    use_case: str = Field(default="generic", pattern=r"^[a-z][a-z0-9_]{0,99}$")


class UpdateSchemaDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, min_length=1, max_length=20000)
    use_case: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,99}$")
    ai_extract_schema: dict[str, ExtractField] = Field(min_length=1, max_length=256)


class ValidateSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Deliberately uncapped (unlike the draft-save request below): an over-limit schema must
    # still be POST-able here so the editor can report *why* it is invalid, rather than being
    # rejected by the request shape before the service gets a chance to explain the violation.
    ai_extract_schema: dict[str, ExtractField] = Field(min_length=1)


class SchemaValidationResponse(BaseModel):
    valid: bool
    depth: int
    max_depth: int
    leaf_count: int
    max_leaves: int
    errors: list[str]


class CloneSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_display_name: str = Field(min_length=1, max_length=200)
    new_schema_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,99}$")


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
    invoice_index: int


class InvoiceSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    file_name: str
    case_id: str | None
    invoice_index: int
    invoice_number: str | None
    invoice_date: date | None
    seller_name: str | None
    currency: str | None
    line_item_count: int
    line_items_sum: Decimal | None
    total_amount: Decimal | None
    reconciliation_delta: Decimal | None
    document_status: Literal["VALIDATED_PASS", "REVIEW_REQUIRED"] | None


class ExtractionResultResponse(BaseModel):
    run: ExtractionRunResponse
    fields: list[ExtractedFieldResponse]
    candidates: list[InvoiceCandidateResponse]


class GenericExtractionResponse(BaseModel):
    """The schema-agnostic replacement for the invoice-only result above (`GET
    /api/extractions/{run_id}`): the run, the schema it used, and the hierarchical result
    exactly as `ai_extract` returned it -- suitable for a recursive results UI to render
    directly (repeated root records as "Record 1", "Record 2", nested objects and arrays as
    nested tables) without any invoice-specific field names.
    """

    run: ExtractionRunResponse
    schema_id: str
    schema_version: int
    root_mode: Literal["SINGLE_RECORD", "REPEATED_RECORDS"]
    result: dict[str, Any]


class ExtractionRunSummaryResponse(BaseModel):
    """One row of the run-centric Results list (`GET /api/extractions`): a run plus the
    document/schema context needed to display and filter it without a per-row round trip.

    Deliberately no raw model result, field values or review counters: list rows stay bounded and
    detail endpoints own field-level evidence and review state.
    """

    extraction_run_id: str
    document_id: str
    document_name: str
    case_id: str | None
    schema_id: str
    schema_version: int
    schema_display_name: str
    status: Literal["RUNNING", "EXTRACTED", "FAILED"]
    started_at: datetime
    completed_at: datetime | None
    is_latest: bool


class ExtractionRunPageResponse(BaseModel):
    items: list[ExtractionRunSummaryResponse]
    next_cursor: str | None


class GenericRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_id: str
    parent_record_id: str | None
    schema_path: str
    instance_path: str
    ordinal: int | None


class GenericFieldResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_id: str
    schema_path: str
    instance_path: str
    field_name: str
    declared_type: str
    value: Any
    value_string: str | None
    confidence_score: float | None
    citation_ids: list[int]
    citations: list[dict[str, Any]]
    validation_status: str | None
    validation_message: str | None


class GenericExtractionRecordsResponse(BaseModel):
    """`GET /api/extractions/{run_id}/records`: the flat, generic record/field tables that
    back a review grid or an export, for any schema shape -- flat, singly-nested, or nested to
    several levels."""

    run: ExtractionRunResponse
    schema_id: str
    schema_version: int
    root_mode: Literal["SINGLE_RECORD", "REPEATED_RECORDS"]
    records: list[GenericRecordResponse]
    fields: list[GenericFieldResultResponse]


class ReviewFieldPolicyResponse(BaseModel):
    confidence_threshold: float
    citation_required: bool


class ExtractionReviewResponse(BaseModel):
    """One read model for rendering extraction values beside their source document."""

    run: ExtractionRunResponse
    document: DocumentResponse
    schema_id: str
    schema_version: int
    root_mode: Literal["SINGLE_RECORD", "REPEATED_RECORDS"]
    result: dict[str, Any]
    fields: list[GenericFieldResultResponse]
    field_policies: dict[str, ReviewFieldPolicyResponse]


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[str] = Field(min_length=1, max_length=500)
    format: Literal["xlsx", "csv"] = "xlsx"


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


class ParseBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[str] = Field(min_length=1, max_length=200)


class ExtractionBatchRequest(ParseBatchRequest):
    schema_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    schema_version: int = Field(ge=1)


class BatchFailureResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: str
    code: str
    message: str


class BatchMemberResponse(BaseModel):
    document_id: str
    run_id: str
    status: str


class BatchResponse(BaseModel):
    """One submission of a set of documents. The execution engine is deliberately not exposed."""

    kind: Literal["parse", "extract"]
    job_run_id: int | None
    requested: int
    accepted: int
    members: list[BatchMemberResponse]
    errors: list[BatchFailureResponse]


class BatchStatusResponse(BaseModel):
    kind: Literal["parse", "extract"]
    job_run_id: int
    total: int
    running: int
    succeeded: int
    failed: int
    members: list[BatchMemberResponse]
