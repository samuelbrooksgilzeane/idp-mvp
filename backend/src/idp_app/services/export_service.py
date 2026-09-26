from __future__ import annotations

import io
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.documents import DocumentServiceError
from idp_app.services.export_sources import ExportSource, ExportSourceRepository
from idp_app.services.exports import (
    ExportTable,
    build_export_tables,
)
from idp_app.services.extraction_result import walk_extraction
from idp_app.services.schema_models import SchemaRecord

_RunTables = tuple[ExtractionRunRecord, SchemaRecord, list[ExportTable]]


@dataclass(frozen=True)
class ExportResult:
    """A generated export plus whether it turned out to span more than one distinct schema
    version -- the caller needs this to pick the right filename/content-type (a single
    workbook/CSV-zip vs. a ZIP of per-schema-version workbooks/CSV-zips)."""

    content: io.BytesIO
    is_multi_schema: bool


class ExportService:
    """Bulk-load selected runs and hand their in-memory projections to the export builders.

    The repository returns every run, schema and document name in one read-only query. The
    retained response is walked locally because export is a read operation: it must never issue
    one detail query per run or attempt to populate cache tables as a side effect.

    Different schema versions can declare the same table name (e.g. two invoice schema
    versions both producing a "Line_Items" table) with different columns, so runs are grouped
    by exact `(schema_id, schema_version)` before building: one group produces today's single
    workbook/CSV-zip; more than one group produces a ZIP containing one workbook/CSV-zip per
    schema version, so distinct schemas' tables are never combined.
    """

    def __init__(self, sources: ExportSourceRepository) -> None:
        self._sources = sources

    async def _load_tables(self, run_ids: list[str]) -> list[_RunTables]:
        requested_ids = list(dict.fromkeys(run_ids))
        sources = await run_in_threadpool(self._sources.get_many, requested_ids)
        by_run_id = {source.run.extraction_run_id: source for source in sources}
        for run_id in requested_ids:
            source = by_run_id.get(run_id)
            if source is None:
                raise DocumentServiceError(
                    "EXTRACTION_RUN_NOT_FOUND", "Extraction run not found.", 404
                )
            if source.run.ai_result is None:
                raise DocumentServiceError(
                    "EXTRACTION_RESULT_UNAVAILABLE",
                    "This extraction run has no retained ai_extract result.",
                    409,
                )
        ordered_sources = [by_run_id[run_id] for run_id in requested_ids]
        return await run_in_threadpool(_build_source_tables, ordered_sources)

    @staticmethod
    def _group_by_schema_version(
        tables_by_run: list[_RunTables],
    ) -> dict[tuple[str, int], list[_RunTables]]:
        groups: dict[tuple[str, int], list[_RunTables]] = {}
        for run, schema, tables in tables_by_run:
            groups.setdefault((run.schema_id, run.schema_version), []).append((run, schema, tables))
        return groups

    async def _export(self, run_ids: list[str], format: str) -> ExportResult:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from idp_app.services.export_writer import write_export

        def generate() -> ExportResult:
            with TemporaryDirectory(prefix="idp-small-export-") as directory:
                destination = Path(directory) / "result"
                multiple = write_export(self.iter_tables(run_ids), destination, format)
                return ExportResult(io.BytesIO(destination.read_bytes()), multiple)

        try:
            return await run_in_threadpool(generate)
        except ValueError as error:
            raise DocumentServiceError("EXPORT_LIMIT", str(error), 422) from error

    def iter_tables(self, run_ids: list[str]):
        """One joined read per bounded page; release retained JSON after each page."""
        requested = list(dict.fromkeys(run_ids))
        for offset in range(0, len(requested), 25):
            page = requested[offset : offset + 25]
            sources = {
                source.run.extraction_run_id: source for source in self._sources.get_many(page)
            }
            for run_id in page:
                source = sources.pop(run_id, None)
                if source is None:
                    raise DocumentServiceError(
                        "EXTRACTION_RUN_NOT_FOUND", "Extraction run not found.", 404
                    )
                if source.run.status != "EXTRACTED" or source.run.ai_result is None:
                    raise DocumentServiceError(
                        "EXTRACTION_RESULT_UNAVAILABLE",
                        "Select completed successful extraction runs.",
                        409,
                    )
                if source.run.schema_hash != source.schema.schema_hash:
                    raise DocumentServiceError(
                        "SCHEMA_MISMATCH", "The pinned schema has changed.", 409
                    )
                yield _build_source_tables([source])[0]

    async def export_workbook(self, run_ids: list[str]) -> ExportResult:
        return await self._export(run_ids, "xlsx")

    async def export_csv_bundle(self, run_ids: list[str]) -> ExportResult:
        return await self._export(run_ids, "csv")


def _build_source_tables(sources: list[ExportSource]) -> list[_RunTables]:
    tables_by_run: list[_RunTables] = []
    for source in sources:
        ai_result = source.run.ai_result
        if ai_result is None:  # Narrowed by `_load_tables`; protects direct helper calls too.
            continue
        records, fields = walk_extraction(source.run, source.schema, ai_result)
        tables = build_export_tables(source.run, records, fields, source.document_name)
        tables_by_run.append((source.run, source.schema, tables))
    return tables_by_run
