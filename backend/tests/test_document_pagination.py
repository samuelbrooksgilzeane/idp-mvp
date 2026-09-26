from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from idp_app.services.document_models import DocumentRecord
from idp_app.services.document_registry import (
    DatabricksDocumentRegistry,
    SQLiteDocumentRegistry,
    document_page_cursor,
)


def record(index: int) -> DocumentRecord:
    now = datetime(2026, 9, 26, tzinfo=UTC)
    return DocumentRecord(
        document_id=f"doc-{index:04}",
        case_id="case-a",
        template_id="invoice_v1",
        use_case="invoice",
        source_path="unused",
        file_name=f"invoice-{index:04}.pdf",
        file_size=1,
        content_sha256=str(index),
        selected_schema_id=None,
        selected_schema_version=None,
        status="UPLOADED",
        uploaded_by="tester",
        uploaded_at=now,
        updated_at=now,
    )


def test_all_metadata_rows_are_reachable_with_equal_timestamps(tmp_path: Path) -> None:
    registry = SQLiteDocumentRegistry(tmp_path / "registry.db")
    for index in range(1000):
        registry.add(record(index))
    cursor = None
    seen = []
    while True:
        rows = registry.list_document_page(None, None, "", cursor, 50)
        assert len(rows) <= 51
        seen.extend(row.document_id for row in rows[:50])
        if len(rows) <= 50:
            break
        cursor = document_page_cursor(rows[49], None, None, "")
    assert seen == [f"doc-{index:04}" for index in reversed(range(1000))]


def test_filters_and_cursor_identity(tmp_path: Path) -> None:
    registry = SQLiteDocumentRegistry(tmp_path / "registry.db")
    registry.add(record(1))
    registry.add(record(2))
    assert len(registry.list_document_page("case-a", "UPLOADED", "0001", None, 50)) == 1
    assert registry.list_document_page(None, None, "%", None, 50) == []
    cursor = document_page_cursor(record(2), None, None, "")
    with pytest.raises(ValueError, match="reset"):
        registry.list_document_page("case-a", None, "", cursor, 50)
    with pytest.raises(ValueError, match="reset"):
        registry.list_document_page(None, None, "", "bad cursor", 50)
    registry.delete("doc-0001")
    assert len(registry.list_document_page(None, None, "", None, 50)) == 1


def test_databricks_page_is_one_bounded_parameterized_query() -> None:
    registry = DatabricksDocumentRegistry(MagicMock(), "warehouse", "catalog", "schema", "idp")
    registry.execute_sql = MagicMock(return_value=[])
    search = "' OR 1=1 --"
    cursor = document_page_cursor(record(2), "case-a", None, search)
    registry.list_document_page("case-a", None, search, cursor, 50)
    registry.execute_sql.assert_called_once()
    statement, values = registry.execute_sql.call_args.args
    assert search not in statement
    assert "CAST(:after_at AS TIMESTAMP)" in statement
    assert "document_id < :after_id" in statement
    assert values["page_limit"] == 51
    assert values["search"] == search
