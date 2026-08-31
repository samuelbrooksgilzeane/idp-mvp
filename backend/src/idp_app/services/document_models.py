from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    case_id: str | None
    template_id: str
    use_case: str
    source_path: str
    file_name: str
    file_size: int
    content_sha256: str
    selected_schema_id: str | None
    selected_schema_version: int | None
    status: str
    uploaded_by: str
    uploaded_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UploadMetadata:
    case_id: str | None
    template_id: str
    use_case: str


@dataclass(frozen=True)
class ParseRunRecord:
    parse_run_id: str
    document_id: str
    content_sha256: str
    parser_version: str
    parsed: dict[str, Any] | None
    document_text: str | None
    page_count: int | None
    page_image_root: str
    parse_error: dict[str, Any] | list[Any] | None
    status: str
    requested_by: str
    job_run_id: int | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class ExtractionRunRecord:
    extraction_run_id: str
    document_id: str
    parse_run_id: str
    schema_id: str
    schema_version: int
    schema_hash: str
    extractor_version: str
    options: dict[str, str]
    ai_result: dict[str, Any] | None
    error_message: str | None
    status: str
    requested_by: str
    job_run_id: int | None
    started_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class ExtractionRunListRecord:
    """A compact, display-safe row for the paginated Results list.

    It deliberately excludes the retained raw model result and every extracted field. Those
    belong to the detail endpoint, not a list that must remain fast as run history grows.
    """

    extraction_run_id: str
    document_id: str
    document_name: str
    case_id: str | None
    schema_id: str
    schema_version: int
    schema_display_name: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    is_latest: bool


@dataclass(frozen=True)
class ExtractedFieldRecord:
    extraction_run_id: str
    document_id: str
    field_path: str
    field_type: str
    value: Any
    value_string: str | None
    confidence_score: float | None
    citation_ids: list[int]
    citations: list[dict[str, Any]]
    extraction_error: str | None


@dataclass(frozen=True)
class InvoiceCandidateRecord:
    case_id: str | None
    document_id: str
    source_path: str
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
    # Which invoice within the extraction run this row describes. A document that states one
    # invoice has a single candidate at index 0; a document stating several has one per invoice.
    invoice_index: int = 0


@dataclass(frozen=True)
class InvoiceLineCandidateRecord:
    """One typed billed line. Candidate data is not approved data."""

    extraction_run_id: str
    document_id: str
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    tax: Decimal | None
    amount: Decimal | None
    # The invoice this line belongs to. Lines number from 1 within their own invoice.
    invoice_index: int = 0


@dataclass(frozen=True)
class ExtractedRecordRow:
    """One node of the recursive extraction result: the document root, a singleton nested
    object, or one item of a repeated array. Generalizes the invoice-specific candidate
    projection so an arbitrary schema (flat or nested to any depth) produces a uniform,
    schema-agnostic record tree.

    `record_id` is deterministic from `run_id + instance_path`, so re-processing the same
    run (a retry) produces identical identifiers rather than duplicates.
    """

    run_id: str
    document_id: str
    record_id: str
    parent_record_id: str | None
    schema_path: str
    instance_path: str
    ordinal: int | None


@dataclass(frozen=True)
class GenericFieldRow:
    """One scalar leaf produced by the generic recursive walker, attached to the
    `extracted_record` that contains it."""

    run_id: str
    document_id: str
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


@dataclass(frozen=True)
class ValidationResultRecord:
    """One immutable observation produced by a validator. It never edits extracted data."""

    validation_run_id: str
    extraction_run_id: str
    document_id: str
    rule_id: str
    field_path: str | None
    validator_type: str
    severity: str
    status: str
    message: str
    actual_value: str | None
    expected_value: str | None
    suggested_value: str | None
    evidence: str | None
    validator_version: str
    prompt_hash: str | None
    created_at: datetime


@dataclass(frozen=True)
class ValidationRunRecord:
    validation_run_id: str
    document_id: str
    extraction_run_id: str
    schema_id: str
    schema_version: int
    schema_hash: str
    validator_version: str
    status: str
    document_status: str
    requested_by: str
    started_at: datetime
    completed_at: datetime | None
