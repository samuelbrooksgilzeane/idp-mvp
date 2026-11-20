# Release evidence — 20 November 2026

First capacity run on real documents. Run and timed by the user on the US workspace; recorded by
Claude Code from the user's report. Timings are the user's observations (rounded), not job-run
exports.

## Target

| Item | Value |
|---|---|
| Workspace | US (paid), deployed from a workspace Git folder with `python3 scripts/deploy_workspace.py us --adopt-app` |
| Branch | `feat/remove-managed-chat` (`2e39d01`: chat removed, 50 MiB upload limit, recovery schedule paused) |
| App | `idp-mvp-dev` (adopted, address kept) |
| Data | `idp_testing_catalog.idp_mvp`, prefix `idp_dev` |
| Settings | `auto_prepare_enabled: "true"`, `upload_parallel_transfers: 3`, `parse_concurrency: 3`, `combined_inference_concurrency: 6`, `parse_dispatch_size: 100`, `recovery_schedule_status: PAUSED` |

## Run 1 — browser upload, 100 real PDFs

Documents: the 100 most recent arXiv `cs.CL` papers (export API, `start=0`), public research
papers, typically 10–40 pages and 1–20 MB each (one sampled file was 18 MB). Exact page and byte
totals were not recorded.

| Measure | Result |
|---|---|
| Files registered | 100 of 100 |
| Upload (browser, 3 transfers in flight) | under 15 minutes, about 9 s per file overall |
| Automatic parsing (`ai_parse_document`, 3 in parallel) | 43 minutes for all 100, about 26 s per paper overall (about 78 s each per parallel slot) |
| Parse failures | 0 |
| Retries needed | none reported |

Upload throughput matches the 29 September measurement (3 files concurrently in about 26 s), with
much larger files here. Parsing, not upload, is the longer stage; `parse_concurrency` (within
`combined_inference_concurrency`) is the lever to test next. The paused recovery schedule did not
matter: the upload's immediate dispatcher wake-up started parsing.

## Implications

- 100 files by browser is proven. At the same rate, 1,000 files would take about 2.5 hours of
  upload with the tab open, so folder import remains the route for large loads.
- Parsing scales with `parse_concurrency`; at 3, 1,000 similar papers would take about 7 hours.

## Still open

- The same run by folder import (a different 100 papers, `start=100`).
- One PDF of about 45 MB by browser and by folder import (the 50 MiB limit and the gateway).
- Extraction, review and export on schema-matched documents (invoices or forms).
- A non-admin colleague using the app (L8's remaining part).
