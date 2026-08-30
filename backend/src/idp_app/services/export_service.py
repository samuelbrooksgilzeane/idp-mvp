from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.documents import DocumentServiceError
from idp_app.services.exports import (
    ExportTable,
    build_csv_bundle,
    build_export_tables,
    build_workbook,
)
from idp_app.services.generic_results import ExtractionResultsService
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
    """Loads each requested run's generic result and hands it to the schema-driven export
    builders in `exports.py` (section 7 of the generalized IDP plan).

    Different schema versions can declare the same table name (e.g. two invoice schema
    versions both producing a "Line_Items" table) with different columns, so runs are grouped
    by exact `(schema_id, schema_version)` before building: one group produces today's single
    workbook/CSV-zip; more than one group produces a ZIP containing one workbook/CSV-zip per
    schema version, so distinct schemas' tables are never combined.
    """

    def __init__(self, results: ExtractionResultsService) -> None:
        self._results = results

    async def _load_tables(self, run_ids: list[str]) -> list[_RunTables]:
        tables_by_run: list[_RunTables] = []
        for run_id in dict.fromkeys(run_ids):
            result = await self._results.get_records(run_id)
            document_name = await self._results.document_name(result.run.document_id)
            tables = await run_in_threadpool(
                build_export_tables, result.run, result.records, result.fields, document_name
            )
            tables_by_run.append((result.run, result.schema, tables))
        return tables_by_run

    @staticmethod
    def _group_by_schema_version(
        tables_by_run: list[_RunTables],
    ) -> dict[tuple[str, int], list[_RunTables]]:
        groups: dict[tuple[str, int], list[_RunTables]] = {}
        for run, schema, tables in tables_by_run:
            groups.setdefault((run.schema_id, run.schema_version), []).append(
                (run, schema, tables)
            )
        return groups

    async def export_workbook(self, run_ids: list[str]) -> ExportResult:
        tables_by_run = await self._load_tables(run_ids)
        groups = self._group_by_schema_version(tables_by_run)
        try:
            if len(groups) <= 1:
                content = await run_in_threadpool(build_workbook, tables_by_run)
                return ExportResult(content=content, is_multi_schema=False)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                for (schema_id, schema_version), group in groups.items():
                    workbook = await run_in_threadpool(build_workbook, group)
                    zf.writestr(f"{schema_id}_v{schema_version}.xlsx", workbook.getvalue())
            archive.seek(0)
            return ExportResult(content=archive, is_multi_schema=True)
        except Exception as error:
            raise DocumentServiceError(
                "EXPORT_FAILED", "The extraction export could not be generated.", 502
            ) from error

    async def export_csv_bundle(self, run_ids: list[str]) -> ExportResult:
        tables_by_run = await self._load_tables(run_ids)
        groups = self._group_by_schema_version(tables_by_run)
        try:
            if len(groups) <= 1:
                content = await run_in_threadpool(build_csv_bundle, tables_by_run)
                return ExportResult(content=content, is_multi_schema=False)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as outer:
                for (schema_id, schema_version), group in groups.items():
                    bundle = await run_in_threadpool(build_csv_bundle, group)
                    with zipfile.ZipFile(bundle) as inner:
                        for name in inner.namelist():
                            outer.writestr(
                                f"{schema_id}_v{schema_version}/{name}", inner.read(name)
                            )
            archive.seek(0)
            return ExportResult(content=archive, is_multi_schema=True)
        except Exception as error:
            raise DocumentServiceError(
                "EXPORT_FAILED", "The extraction export could not be generated.", 502
            ) from error
