from dataclasses import dataclass
from datetime import datetime
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
