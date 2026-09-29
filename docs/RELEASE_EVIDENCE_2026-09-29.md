# Release evidence — 29 September 2026

Verifier: Codex with local recovery, capacity and runbook subagents. No agent-uploaded PDFs,
real-document inference, capacity workload, indexing or recovery schedule activation.

## Target and snapshot

- Profile `idp-mvp`; workspace `https://dbc-97e4a372-40b1.cloud.databricks.com` (ID `7474660341420973`).
- App `idp-mvp-dev`; catalog/schema `workspace.idp_mvp`; prefix `idp_dev`.
- Source volume `workspace.idp_mvp.idp_source`; artifact volume `workspace.idp_mvp.idp_artifacts`.
- Warehouse `647704f77f24020a`: maximum one cluster, 10-minute auto-stop.
- Parser `1021425465418844`; extractor `948342095836775`; dispatcher `727011738005293`;
  exporter `549578235955089`; bootstrap `297705320672479`.
- Existing Genie space `01f1ba8f44851508b81fcc9c9a013451`; freshly exported before planning.
- Existing app principal `e77ceaac-6b84-4a26-8d39-bb38d180bbc9` (numeric ID `70369812436547`).
- Base commit `c4ea9e89eed6bce798f40257d632a5b615b29a22` plus reviewed working-tree changes.
  Runtime/source snapshot SHA-256 `2dec445984e33ba0ae59edc56aa842439a1212fe9da75425b4e891a918041d42`:
  [file manifest](evidence/release-snapshot-2026-09-29.json). Pre-existing documentation reorganization
  and schema-save changes were preserved. No commit was created.

## Preparation and deployment

App compute successfully restarted after the prior workspace-status stop. The previous snapshot
became healthy. Additive SQL applied successfully for all five upload/work tables, two export
tables and two viewer tables. Four generic Genie views created; generic record/field columns
were already present. Metadata inventory verified 27 project tables/views.

Narrow SELECT/MODIFY grants on the nine new tables, USE CATALOG/SCHEMA, required reporting SELECT,
artifact-volume READ/WRITE and dispatcher/export CAN_MANAGE_RUN were applied and read back for
only the existing app identity. Existing 20 App resource bindings were preserved; additional grants
are explicit UC/Jobs permissions, not extra bindings. Newly created views received explicit SELECT.

The bundle plan contained two updates (app configuration and parser viewer flag), no resource
creation/deletion, and no Genie change. Bundle deployment succeeded (67 files uploaded, 30 obsolete
workspace files removed; these are source files, not document-volume data). Activation succeeded: deployment `01f1bc532ec01c13a89d8c0a68ccd4c2`; app RUNNING, compute ACTIVE.
Authenticated health, upload limits and Genie configuration returned HTTP 200. Deployed
`index-BXilEIxo.js` and `index-BV33GSRk.css` SHA-256 hashes match the local production build.

Viewer projections enabled in app and parser configuration. Automatic preparation, bulk extraction
and bulk export remain false; dispatcher recovery remains PAUSED. Upload limit 1,000 members,
25 MiB per file, three frontend transfers. Flags expose capability; they do not establish capacity.
The existing Genie embed/open URLs were verified in `/api/app-config`; allowed-origin and colleague
embedded access remain live UI acceptance tasks.

Viewer projection backfill used one retained one-page result, parse
`3d411f3e-4fee-477c-9222-d0f35741758c`, document `b755bbad-eb16-50da-86f9-140f76ef5664`.
The resulting manifest and 24 projected elements were read back in 11.328 seconds, including
retained-result read and projection writes. Zero inference calls.

## Local validation and fixes

- 248 backend tests and 81 frontend tests passed. Python/frontend lint, mypy (77 files),
  TypeScript, production build and offline configuration validation passed.
- Fixed retry after a PDF has already been removed but registry deletion failed; NotFound is
  idempotent, while permission/service failures still propagate.
- Fixed mock parse completion race with an atomic SQLite run/document terminal-state transaction;
  deterministic regression probes the former projection gap.
- Added missing durable extraction `work_batches` to governed table inventory.
- Verified failed-only retry leaves successful members intact; batch/export owner restrictions;
  historical extraction keeps its original parse ID, schema version/hash and result after newer runs.
- Fixed upload progress infinite polling on a repeated cursor and quadratic local/server reconciliation.
- Mocked 1,000-file test verified exactly three maximum active transfers, ten progress API pages,
  all outcomes restored and all 40 UI pages reachable with 25 rows rendered at a time.
- Reconciled obsolete migration, Job task and safe export-filename assertions with current contracts.
- [Synthetic export benchmark](EXPORT_BENCHMARK.md): 1,000 documents × ten child records,
  CSV 0.55 s / XLSX 1.19 s; outputs and local spool cleanup verified. This excludes live SQL, network,
  app concurrency and remote artifact storage.

## Outstanding live acceptance

The [user smoke procedure](RELEASE_RUNBOOK.md) and [capacity procedure](CAPACITY_TEST_PROCEDURE.md)
are ready. User upload, inference, interruption/retry, deletion and colleague tests remain open.
No 1,000-file live acceptance or Free Edition capacity claim is made. No app stop/start was performed
while the user was testing. Browser render/decode, cold-warehouse latency, deployed durable export
expiry, real queue recovery and approved content-search sync are separate remaining checks.

Export expiry blocks download but does not physically remove stored artifacts. Support/retention
ownership must still be chosen. Proposed warm p95 API targets: document/results/schema lists ≤2 s,
viewer metadata ≤2 s, schema save/publish ≤3 s; these are proposals, not accepted SLAs. Cold-start
and browser rendering targets require measurements before agreement.

## Authenticated performance sample

[HTTP evidence](evidence/release-http-2026-09-29.json) records elapsed time, payload bytes and
Server-Timing per request. The warehouse was already running. These are three navigation reads
and two disposable schema lifecycles, not statistically reliable p95/cold-start or browser results.

| Operation | First request | Repeats | SQL statements per request |
| --- | ---: | ---: | ---: |
| Documents list | 1.862 s | 0.600 / 0.572 s | 1 |
| Results list | 1.949 s | 0.654 / 0.667 s | 1 |
| Schemas list | 1.015 s | 0.614 / 0.617 s | 1 |
| Viewer metadata | 2.219 s | 1.578 / 1.602 s | 3 |
| Schema create | 5.542 s | 6.620 s | 3 |
| Schema draft save | 6.140 s | 6.278 / 5.348 / 5.713 s | 3 |
| Schema publish | 6.167 s | 4.812 s | 4 |

One retained page image transferred 522,078 bytes in 2.658 s (three SQL statements); browser decode
and render were not measured. Warm list/viewer samples are within the proposed targets; schema
mutations exceed the proposed 3-second target, with nearly all server duration attributed to SQL.
The earlier save optimization is deployed, but further schema mutation performance work remains open.
Do not treat these few successful samples as the complete performance acceptance gate.

Two clearly labelled `Agent release measurement 20260929 …` version-1 schemas were created and
published. Their IDs are in the HTTP evidence. They contain only a synthetic reference field;
user schema definitions were not modified and no extraction used them. They remain as identifiable
disposable fixtures; removal is not needed for the app to operate.
