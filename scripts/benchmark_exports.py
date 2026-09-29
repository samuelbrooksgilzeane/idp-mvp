"""Local-only synthetic retained-response export benchmark; never connects to a workspace."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import platform
import resource
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
from openpyxl import load_workbook  # type: ignore[import-untyped]
from idp_app.services.document_models import ExtractionRunRecord
from idp_app.services.export_service import ExportService
from idp_app.services.export_sources import ExportSource
from idp_app.services.export_writer import WRITER_VERSION, write_export
from idp_app.services.schema_models import ExtractField, SchemaRecord

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def identity(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"idp-synthetic-benchmark:{label}"))


class SyntheticSources:
    """Generate bounded pages with the same retained JSON contract as the repository."""

    def __init__(self, records: int):
        self.records = records
        self.calls = 0
        self.max_page = 0
        self.schema = SchemaRecord(
            schema_id="benchmark",
            schema_version=1,
            display_name="Synthetic benchmark",
            use_case="generic",
            ai_extract_schema={
                "records": ExtractField(
                    type="array",
                    description="Synthetic rows",
                    items=ExtractField(
                        type="object",
                        description="Row",
                        properties={
                            "label": ExtractField(type="string", description="Label"),
                            "amount": ExtractField(type="number", description="Amount"),
                        },
                    ),
                )
            },
            instructions="Synthetic only",
            field_policies={},
            document_rules=[],
            schema_hash="synthetic-v1",
            status="PUBLISHED",
            created_by="local-benchmark",
            created_at=NOW,
        )

    def get_many(self, run_ids: list[str]) -> list[ExportSource]:
        self.calls += 1
        self.max_page = max(self.max_page, len(run_ids))

        def scalar(value):
            return {"value": value, "confidence_score": 0.9, "citation_ids": []}

        return [
            ExportSource(
                run=ExtractionRunRecord(
                    extraction_run_id=run_id,
                    document_id=identity(f"doc:{run_id}"),
                    parse_run_id=identity(f"parse:{run_id}"),
                    schema_id="benchmark",
                    schema_version=1,
                    schema_hash="synthetic-v1",
                    extractor_version="synthetic",
                    options={},
                    ai_result={
                        "response": {
                            "records": [
                                {
                                    "label": scalar(f"Row {index}: {run_id}"),
                                    "amount": scalar(index + 0.25),
                                }
                                for index in range(self.records)
                            ]
                        },
                        "error_message": None,
                        "metadata": {"citations": []},
                    },
                    error_message=None,
                    status="EXTRACTED",
                    requested_by="local-benchmark",
                    job_run_id=None,
                    started_at=NOW,
                    completed_at=NOW,
                ),
                schema=self.schema,
                document_name=f"synthetic-{run_id}.pdf",
            )
            for run_id in run_ids
        ]


def peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def benchmark(documents: int, records: int, output_format: str) -> dict:
    sources = SyntheticSources(records)
    ids = [identity(f"run:{index}") for index in range(documents)]
    disk_peak = 0
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="idp-export-benchmark-") as directory:
        root = Path(directory)
        destination = root / (
            "export.xlsx" if output_format == "xlsx" else "export.zip"
        )

        def sample_disk():
            nonlocal disk_peak
            while not stop.is_set():
                total = 0
                for path in root.rglob("*"):
                    try:
                        if path.is_file():
                            total += path.stat().st_size
                    except FileNotFoundError:
                        pass  # Writer removes its private spools as it completes.
                disk_peak = max(disk_peak, total)
                stop.wait(0.01)

        baseline_rss = peak_rss_bytes()
        monitor = threading.Thread(target=sample_disk, daemon=True)
        monitor.start()
        started = time.perf_counter()
        try:
            write_export(
                ExportService(sources).iter_tables(ids), destination, output_format
            )
        finally:
            duration = time.perf_counter() - started
            writer_peak_rss = peak_rss_bytes()
            stop.set()
            monitor.join()
        size = destination.stat().st_size
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if output_format == "xlsx":
            workbook = load_workbook(destination, read_only=True, data_only=True)
            try:
                counts = {
                    name: sum(1 for _ in workbook[name].values) - 1
                    for name in ("Document", "Records")
                }
            finally:
                workbook.close()
        else:
            with zipfile.ZipFile(destination) as archive:
                counts = {}
                for name in ("Document", "Records"):
                    with archive.open(f"{name}.csv") as stream:
                        counts[name] = (
                            sum(1 for _ in csv.reader(io.TextIOWrapper(stream))) - 1
                        )
        assert counts == {"Document": documents, "Records": documents * records}, counts
        assert sources.max_page <= 25
        assert not list(root.glob("idp-export-*")), (
            "Temporary writer spools were not removed"
        )
    return {
        "format": output_format,
        "documents": documents,
        "records_per_document": records,
        "verified_rows": counts,
        "source_page_calls": sources.calls,
        "max_source_page": sources.max_page,
        "duration_seconds": round(duration, 4),
        "baseline_process_high_water_rss_bytes": baseline_rss,
        "writer_process_peak_rss_bytes": writer_peak_rss,
        "sampled_peak_temp_disk_bytes": max(disk_peak, size),
        "disk_sample_interval_ms": 10,
        "output_bytes": size,
        "output_sha256": digest,
        "spools_cleaned": True,
        "writer_version": WRITER_VERSION,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=1000)
    parser.add_argument("--records-per-document", type=int, default=10)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--worker-format", choices=("xlsx", "csv"), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    if not 1 <= args.documents <= 1000 or not 1 <= args.records_per_document <= 1000:
        parser.error("Use 1–1000 documents and 1–1000 records per document")
    if args.worker_format:
        print(
            json.dumps(
                benchmark(args.documents, args.records_per_document, args.worker_format)
            )
        )
        return
    results = []
    for output_format in ("csv", "xlsx"):
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--documents",
                str(args.documents),
                "--records-per-document",
                str(args.records_per_document),
                "--worker-format",
                output_format,
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        results.append(json.loads(process.stdout))
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "synthetic_local_only": True,
        "results": results,
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
