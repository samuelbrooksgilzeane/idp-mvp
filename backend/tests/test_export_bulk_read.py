from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any, cast

from openpyxl import load_workbook  # type: ignore[import-untyped]

from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.export_service import ExportService
from idp_app.services.export_sources import DatabricksExportSourceRepository


class RecordingSqlClient:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def execute_sql(
        self, statement: str, values: dict[str, object] | None = None
    ) -> list[list[str]]:
        self.calls.append((statement, values))
        return self.rows


def _source_row(run_id: str, document_id: str, document_name: str, total: int) -> list[str]:
    now = datetime(2026, 8, 31, 12, tzinfo=UTC).isoformat()
    run_values: list[Any] = [
        run_id,
        document_id,
        f"parse-{run_id}",
        "invoice",
        "3",
        "schema-hash",
        "2.1",
        json.dumps({"version": "2.1"}),
        json.dumps(
            {
                "response": {
                    "total": {
                        "value": total,
                        "confidence_score": 1.0,
                        "citation_ids": [],
                    }
                }
            }
        ),
        "",
        "EXTRACTED",
        "tester@example.com",
        "123",
        now,
        now,
    ]
    schema_values: list[Any] = [
        "invoice",
        "3",
        "Invoice v3",
        "invoice",
        json.dumps({"total": {"type": "number", "description": "Stated total."}}),
        "Extract only stated values.",
        json.dumps(
            {
                "total": {
                    "required": False,
                    "confidence_threshold": 0.0,
                    "citation_required": False,
                    "risk_tier": "low",
                }
            }
        ),
        "[]",
        "schema-hash",
        "PRODUCTION",
        "bootstrap",
        now,
        "Invoice schema",
        now,
    ]
    return [str(value) for value in [*run_values, *schema_values, document_name]]


def test_multi_run_export_uses_one_read_only_joined_statement() -> None:
    # Return rows in database order rather than request order. ExportService must restore the
    # caller's order after the one bulk query.
    sql = RecordingSqlClient(
        [
            _source_row("run-a", "doc-a", "a.pdf", 100),
            _source_row("run-b", "doc-b", "b.pdf", 200),
        ]
    )
    repository = DatabricksExportSourceRepository(
        cast(DatabricksDocumentRegistry, sql), "workspace", "idp_mvp", "idp_dev"
    )

    result = asyncio.run(ExportService(repository).export_workbook(["run-b", "run-a", "run-b"]))

    assert len(sql.calls) == 1
    statement, parameters = sql.calls[0]
    assert "JOIN workspace.idp_mvp.idp_dev_documents" in statement
    assert "JOIN workspace.idp_mvp.idp_dev_schema_registry" in statement
    assert "extracted_records" not in statement
    assert "extracted_fields" not in statement
    assert "MERGE" not in statement
    assert parameters == {"run_id_0": "run-b", "run_id_1": "run-a"}

    workbook = load_workbook(result.content, data_only=True)
    rows = list(workbook["Document"].values)
    header = list(rows[0])
    run_id_column = header.index("_extraction_run_id")
    document_name_column = header.index("_document_name")
    total_column = header.index("total")
    assert [row[run_id_column] for row in rows[1:]] == ["run-b", "run-a"]
    assert [row[document_name_column] for row in rows[1:]] == ["b.pdf", "a.pdf"]
    assert [row[total_column] for row in rows[1:]] == [200, 100]
