"""Metadata-only bulk resolution; no PDF upload, Job or AI execution."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from idp_app.services.document_models import DocumentRecord, ParseRunRecord
from idp_app.services.document_registry import DatabricksDocumentRegistry, SQLiteDocumentRegistry
from idp_app.services.extraction_inputs import resolve_extraction_inputs
from idp_app.services.parse_runs import DatabricksParseRunRepository, SQLiteParseRunRepository
from idp_app.services.schema_registry import SQLiteSchemaRepository
from idp_app.services.schemas import load_source_manifests


def fixture(tmp_path, count=3):
    path = tmp_path / "metadata.sqlite3"
    documents, parses = SQLiteDocumentRegistry(path), SQLiteParseRunRepository(path)
    schemas = SQLiteSchemaRepository(path)
    schema = schemas.register(load_source_manifests()[0], "tester")
    now = datetime.now(UTC)
    for index in range(count):
        document = DocumentRecord(
            str(index),
            None,
            "invoice_v1",
            "invoice",
            "unused.pdf",
            "metadata.pdf",
            1,
            f"hash-{index}",
            None,
            None,
            "PARSED",
            "tester",
            now,
            now,
        )
        documents.add(document)
        parses.create(
            ParseRunRecord(
                f"old-{index}",
                str(index),
                document.content_sha256,
                "2",
                {"retained": "not loaded by bulk lookup"},
                "not loaded",
                1,
                "unused",
                None,
                "SUCCESS",
                "tester",
                None,
                now,
                now,
            )
        )
    return documents, parses, schema


def test_thousand_metadata_members_use_twenty_bounded_reads(tmp_path):
    documents, parses, schema = fixture(tmp_path, 1000)
    with (
        patch.object(documents, "get_many", wraps=documents.get_many) as docs_read,
        patch.object(
            parses, "latest_successful_references", wraps=parses.latest_successful_references
        ) as parses_read,
    ):
        inputs, errors = resolve_extraction_inputs(
            documents, parses, schema, [str(i) for i in range(1000)] + ["0"]
        )
    assert not errors and len(inputs) == 1000
    assert docs_read.call_count == parses_read.call_count == 10
    assert all(len(call.args[0]) == 100 for call in docs_read.call_args_list)
    assert not hasattr(inputs[0].parse, "parsed")
    assert not hasattr(inputs[0].parse, "document_text")


def test_pinned_input_survives_reparse_and_reports_ineligible_members(tmp_path):
    documents, parses, schema = fixture(tmp_path)
    inputs, errors = resolve_extraction_inputs(documents, parses, schema, ["0"])
    assert not errors
    original = parses.get("old-0")
    parses.create(replace(original, parse_run_id="new-0", completed_at=datetime.now(UTC)))
    assert inputs[0].parse.parse_run_id == "old-0"
    assert parses.latest_successful_references(["0"])["0"].parse_run_id == "new-0"
    documents.delete("1")
    original = parses.get("old-2")
    parses.create(
        replace(
            original,
            parse_run_id="new-2",
            content_sha256="different",
            completed_at=datetime.now(UTC),
        )
    )
    resolved, failures = resolve_extraction_inputs(
        documents, parses, schema, ["0", "1", "2", "missing"]
    )
    assert [item.document.document_id for item in resolved] == ["0"]
    assert [item.code for item in failures] == [
        "DOCUMENT_NOT_FOUND",
        "PARSE_SOURCE_MISMATCH",
        "DOCUMENT_NOT_FOUND",
    ]


def test_empty_and_oversize_inputs_do_not_query(tmp_path):
    documents, parses, schema = fixture(tmp_path, 0)
    with patch.object(documents, "get_many") as read:
        assert resolve_extraction_inputs(documents, parses, schema, []) == ([], [])
        with pytest.raises(ValueError, match="1,000"):
            resolve_extraction_inputs(documents, parses, schema, [str(i) for i in range(1001)])
        read.assert_not_called()


def test_databricks_bulk_queries_are_parameterized_bounded_and_metadata_only():
    # Construct against a stubbed SQL boundary: never creates a workspace client.
    documents = DatabricksDocumentRegistry(None, "warehouse", "catalog", "project", "idp")
    parses = DatabricksParseRunRepository(documents, "catalog", "project", "idp")
    identities = [str(i) for i in range(250)] + ["a' OR 1=1 --"]
    with patch.object(documents, "execute_sql", return_value=[]) as sql:
        assert documents.get_many(identities) == {}
        assert parses.latest_successful_references(identities) == {}
        assert sql.call_count == 6
        for call in sql.call_args_list:
            statement, values = call.args
            assert len(values) <= 100
            assert "a' OR" not in statement
        for call in sql.call_args_list[3:]:
            assert "ROW_NUMBER()" in call.args[0]
            assert "document_text" not in call.args[0]
            assert "TO_JSON(parsed)" not in call.args[0]
