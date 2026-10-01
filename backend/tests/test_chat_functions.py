"""Offline checks of the document chat's UC function template; no Spark or workspace."""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "databricks_etl" / "sql" / "create_chat_functions.sql"
VIEWS = ROOT / "databricks_etl" / "sql" / "create_chat_views.sql"
FUNCTIONS = (
    "chat_document_fields",
    "chat_find_documents",
    "chat_invoices",
    "chat_invoice_totals",
    "chat_case_documents",
)


def load_task():
    name = "create_chat_functions"
    spec = importlib.util.spec_from_file_location(name, ROOT / "databricks_etl/src" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def render(prefix: str = "idp_dev") -> list[str]:
    task = load_task()
    return task.render_statements(
        TEMPLATE.read_text(encoding="utf-8"), "workspace", "idp_mvp", prefix
    )


def test_template_renders_one_qualified_function_per_statement_in_dependency_order() -> None:
    statements = render()

    created = [
        re.match(r"CREATE OR REPLACE FUNCTION `workspace`\.`idp_mvp`\.`idp_dev_(\w+)`\(", s)
        for s in statements
    ]
    assert [match.group(1) for match in created if match] == list(FUNCTIONS)
    # chat_invoice_totals reads chat_invoices, so it must be created after it.
    assert "`workspace`.`idp_mvp`.`idp_dev_chat_invoices`(supplier" in statements[3]


def test_every_referenced_object_is_prefixed_and_quoted() -> None:
    for statement in render("idp_prod"):
        assert "{object:" not in statement
        for name in re.findall(r"`workspace`\.`idp_mvp`\.`(\w+)`", statement):
            assert name.startswith("idp_prod_chat_")
        # Only the chat views and functions are read, never base tables.
        assert "_extracted_fields" not in statement
        assert "`idp_prod_documents`" not in statement


def test_functions_are_read_only_commented_and_name_their_sources() -> None:
    for statement in render():
        upper = " ".join(statement.upper().split())
        assert "COMMENT '" in statement.split("RETURNS TABLE")[1]
        for word in ("INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "EXECUTE IMMEDIATE"):
            assert f" {word} " not in f" {upper} "
        if "invoice_totals" in statement:
            assert "document_ids ARRAY<STRING>, file_names ARRAY<STRING>" in statement
        else:
            assert "document_id STRING, file_name STRING" in statement
        if "invoice_totals" not in statement:  # totals read chat_invoices, which filters
            assert "status <> 'DELETED'" in statement


def test_totals_never_sum_across_currencies() -> None:
    totals = next(s for s in render() if "idp_dev_chat_invoice_totals`(" in s.splitlines()[0])
    assert "GROUP BY seller_name, currency" in totals


def test_chat_fields_view_exposes_legacy_paths() -> None:
    assert "f.field_path, f.field_type" in VIEWS.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("catalog", "schema", "prefix", "path"),
    [
        ("workspace", "idp_mvp", "idp-dev", "create_chat_functions.sql"),
        ("work`space", "idp_mvp", "idp_dev", "create_chat_functions.sql"),
        ("workspace", "idp_mvp", "idp_dev", "other.sql"),
    ],
)
def test_rejects_untrusted_parameters(catalog, schema, prefix, path) -> None:
    task = load_task()
    with pytest.raises(ValueError):
        task.validate_parameters(task.Parameters(catalog, schema, prefix, Path(path)))


def test_rejects_malformed_placeholder() -> None:
    task = load_task()
    with pytest.raises(ValueError):
        task.render_statements("SELECT * FROM {object:Bad-Name};", "c", "s", "p")
