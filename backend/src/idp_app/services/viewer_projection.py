"""Immutable page projections, built from retained parser output without AI inference."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

VERSION = 1


def project(run):
    from idp_app.services.viewer import ParsedPage, _parsed_elements, _parsed_pages

    pages = _parsed_pages(run.parsed)
    by_page = {page_id: [] for page_id, _ in pages}
    for element in _parsed_elements(run.parsed):
        boxes: dict[int, list] = {}
        for box in element.boxes:
            boxes.setdefault(box.page_id, []).append(box)
        for page_id, page_boxes in boxes.items():
            if page_id in by_page:
                by_page[page_id].append(replace(element, boxes=page_boxes))
    metadata = [
        ParsedPage(
            page_id,
            index + 1,
            len(by_page[page_id]),
            sorted({e.element_type for e in by_page[page_id]}),
            image,
        )
        for index, (page_id, image) in enumerate(pages)
    ]
    return metadata, by_page


class ViewerProjection:
    def __init__(self, path: Path | None = None, sql: Any = None, namespace: str = ""):
        self.path, self.sql = path, sql
        self.manifests = f"{namespace}_parsed_page_manifest"
        self.elements = f"{namespace}_parsed_page_elements"
        if path:
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS parsed_page_manifest
                    (parse_run_id TEXT PRIMARY KEY, payload TEXT);
                    CREATE TABLE IF NOT EXISTS parsed_page_elements
                    (parse_run_id TEXT, page_id INTEGER, payload TEXT,
                     PRIMARY KEY(parse_run_id, page_id));
                """)

    def manifest(self, identity: str):
        if self.path:
            with sqlite3.connect(self.path) as conn:
                rows = conn.execute(
                    "SELECT payload FROM parsed_page_manifest WHERE parse_run_id=?", (identity,)
                ).fetchall()
        else:
            rows = self.sql.execute_sql(
                f"SELECT payload FROM {self.manifests} WHERE parse_run_id=:id LIMIT 2",
                {"id": identity},
            )
        if len(rows) > 1:
            raise RuntimeError("Duplicate viewer projection")
        data = json.loads(rows[0][0]) if rows else None
        return data if data and data["version"] == VERSION else None

    def page(self, identity: str, page_id: int):
        if self.path:
            with sqlite3.connect(self.path) as conn:
                rows = conn.execute(
                    "SELECT payload FROM parsed_page_elements WHERE parse_run_id=? AND page_id=?",
                    (identity, page_id),
                ).fetchall()
        else:
            rows = self.sql.execute_sql(
                f"SELECT payload FROM {self.elements} "
                "WHERE parse_run_id=:id AND page_id=CAST(:page AS INT) LIMIT 2",
                {"id": identity, "page": page_id},
            )
        if len(rows) != 1:
            raise RuntimeError("Incomplete viewer projection; rebuild from retained parse")
        return json.loads(rows[0][0])

    def build(self, run) -> None:
        if run.status != "SUCCESS" or run.parsed is None:
            raise ValueError("Projection requires a successful retained parse")
        if self.manifest(run.parse_run_id):
            return
        pages, elements = project(run)
        payload = json.dumps(
            {
                "version": VERSION,
                "document_id": run.document_id,
                "pages": [asdict(page) for page in pages],
            }
        )
        members = [
            (run.parse_run_id, key, json.dumps([asdict(e) for e in values]))
            for key, values in elements.items()
        ]
        if self.path:
            with sqlite3.connect(self.path) as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO parsed_page_elements VALUES (?, ?, ?)", members
                )
                conn.execute(
                    "INSERT OR REPLACE INTO parsed_page_manifest VALUES (?, ?)",
                    (run.parse_run_id, payload),
                )
        else:
            for identity, page, content in members:
                self.sql.execute_sql(
                    f"MERGE INTO {self.elements} t USING "
                    "(SELECT :id parse_run_id, CAST(:page AS INT) page_id, :payload payload) s "
                    "ON t.parse_run_id=s.parse_run_id AND t.page_id=s.page_id "
                    "WHEN MATCHED THEN UPDATE SET payload=s.payload WHEN NOT MATCHED THEN INSERT *",
                    {"id": identity, "page": page, "payload": content},
                )
            # Readiness marker is published only after all page writes succeed.
            self.sql.execute_sql(
                f"MERGE INTO {self.manifests} t USING "
                "(SELECT :id parse_run_id, :payload payload) s ON t.parse_run_id=s.parse_run_id "
                "WHEN NOT MATCHED THEN INSERT *",
                {"id": run.parse_run_id, "payload": payload},
            )
