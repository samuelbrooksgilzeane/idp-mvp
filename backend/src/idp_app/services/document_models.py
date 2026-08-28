from dataclasses import dataclass
from datetime import datetime


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
