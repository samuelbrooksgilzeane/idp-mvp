# Local synthetic export benchmark

On 2026-09-29, both CSV ZIP and XLSX exports passed a local 1,000-document benchmark with
10 repeated records per document. Each output was reopened and verified to contain exactly
1,000 document rows and 10,000 child rows. Source loading used 40 pages, each bounded to
25 retained responses. Writer temporary spools were removed successfully.

| Format | Writer duration | Process peak RSS | Sampled peak temporary disk | Output bytes |
| --- | ---: | ---: | ---: | ---: |
| CSV ZIP | 0.5503 s | 114,081,792 bytes | 7,775,448 bytes | 438,528 |
| XLSX | 1.1938 s | 112,410,624 bytes | 15,023,713 bytes | 801,885 |

Environment: macOS 15.3.1, arm64, Python 3.13.1; writer version 2. Each format ran in a fresh
Python process. Baseline process high-water RSS was 112,050,176 bytes for CSV and 109,346,816
bytes for XLSX. See the [machine-readable evidence](evidence/export-benchmark-2026-09-29.json).

## Reproduce

From `idp-mvp`, using the existing backend environment:

```sh
backend/.venv/bin/python scripts/benchmark_exports.py --documents 1000 --records-per-document 10 --output /tmp/idp-export-benchmark.json
```

The script generates synthetic retained-response JSON in bounded pages and calls the production
`ExportService.iter_tables` path, extraction walker, relational table builder, and disk-backed
writer. It uses no workspace settings, SQL, remote data, PDF uploads, inference, or network calls.
Synthetic documents are identifiers only; no source PDF is created. Export artifacts are deleted
after validation; only the optional JSON report is retained.

The workload uses one schema version, two scalar fields per repeated child record, and no
citations. IDs and data are deterministic. ZIP/XLSX container timestamps can change output hashes
between runs; hashes identify the measured artifact, not a reproducible byte-for-byte fixture.

## Measurement limits

This is one local correctness/performance sample per format, not a p50/p95 or throughput SLA.
Duration includes synthetic response generation, conversion, SQLite spooling, and file writing;
it excludes process startup/imports and final output verification. Peak RSS is the operating
system process high-water mark captured immediately after writing, including imports and the
monitor thread. Temporary disk is logical file size sampled every 10 ms, including output and
spools; it is a lower bound on the instantaneous peak and does not measure allocated filesystem
blocks. Monitoring adds some overhead.

The synthetic repository replaces storage reads, so results do not measure Databricks SQL latency,
artifact upload/download, API memory, queue recovery, multiple schema groups, wide schemas,
very large individual values, or production compute. Existing writer tests cover row splitting,
formula-like literal cells, column discovery, oversize values, and disk-budget rejection separately.
Live export acceptance remains in the [release checklist](RELEASE_CHECKLIST.md); follow the
[release runbook](RELEASE_RUNBOOK.md) and keep real document testing user-owned.
