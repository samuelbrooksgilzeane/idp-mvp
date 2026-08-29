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
