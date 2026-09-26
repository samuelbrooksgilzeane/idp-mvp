"""Offline runtime guards; no Spark, workspace, PDFs or inference."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_runtime(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "databricks_etl/src" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_extraction_keeps_pinned_success_after_newer_parse():
    runtime = load_runtime("extract_document")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE runs (parse_run_id, document_id, content_sha256, status)")
    db.executemany(
        "INSERT INTO runs VALUES (?, ?, ?, ?)",
        [
            ("old", "doc", "hash", "SUCCESS"),
            ("new", "doc", "hash", "SUCCESS"),
            ("failed", "doc", "hash", "FAILED"),
        ],
    )

    class Session:
        def sql(self, query, args):
            self.cursor = db.execute(query, args)
            return self

        def first(self):
            return self.cursor.fetchone()

    session = Session()
    runtime.require_pinned_parse(session, "runs", "old", "doc", "hash")
    for run, document, digest in [
        ("failed", "doc", "hash"),
        ("old", "other", "hash"),
        ("old", "doc", "changed"),
    ]:
        with pytest.raises(ValueError, match="pinned successful parse"):
            runtime.require_pinned_parse(session, "runs", run, document, digest)
    db.close()


def test_combined_inference_budget():
    runtime = load_runtime("work_limits")
    runtime.validate_capacity(1, 1, 2)
    for values in [(2, 1, 2), (0, 1, 2), (1, -1, 2)]:
        with pytest.raises(ValueError):
            runtime.validate_capacity(*values)
