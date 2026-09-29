# Upload capacity and recovery validation

Use the [release runbook](RELEASE_RUNBOOK.md) for deployment, recovery, and rollback checks;
record acceptance status in the [release checklist](RELEASE_CHECKLIST.md).

## Local evidence — 2026-09-29

The frontend suite passed 81 tests across 23 files, including all nine focused upload tests.
Coverage includes the following entirely mocked checks.
No PDF was uploaded and no workspace/database request was made by these tests.

- A 1,000-file manifest completes with 1,000 distinct document outcomes, at most three concurrent
  one-file transfers, ten 100-row progress pages, and all outcomes restored by a new manager.
- A deployed file-count limit is enforced before a manifest request is sent.
- The progress component exposes all 40 pages while rendering only 25 file rows at a time.
- A lost upload response is resolved through item lookup before sending the PDF again.
- A lost manifest response retains the same request identity on retry.
- Refresh and reselection resend only unfinished files; pause lets active transfers finish.
- Repeated progress cursors now fail explicitly while retaining saved outcomes, preventing an
  unbounded polling loop. Refresh reconciliation uses a keyed map rather than repeated scans.

Run from `idp-mvp/frontend`: `npm test`, `npm run typecheck`, and `npm run lint`.
These checks establish bounded transfer concurrency and rendered row count, not real browser/API
heap usage, gateway throughput, workspace capacity, or processing throughput.

## User-run staged procedure

Document testing is user-owned. Do not automatically run any stage or create additional copies of
documents to reach a target count. A live 1,000-file acceptance run remains pending and requires
an eligible workspace with an agreed usage budget; a Free Edition deployment is not evidence of
that budget. Keep parsing/extraction disabled for upload-only capacity measurement.

1. Read the deployed upload limits once. Record deployment revision, workspace tier, maximum
   files, per-file byte limit, and automatic preparation state. Stop if preparation is unexpectedly
   enabled or the intended dataset exceeds the configured limits.
2. With a user-selected small batch, record baseline memory and start time. Verify progress,
   navigation away/back, pause/resume, and each file's terminal outcome. Stop on quota, 429,
   gateway/service errors, or unexpected processing; investigate before another batch.
3. For a separately selected recovery batch, refresh with unfinished files, reselect the originals,
   and confirm completed files were skipped. Record client file IDs, document IDs, and retries.
   Do not deliberately interrupt a large live batch to test recovery.
4. Only when the user chooses to continue within their budget, increase batch size gradually.
   Each stage must reconcile fully before the next; reaching 1,000 is not required for ordinary
   exploratory testing and cannot be claimed from the synthetic test.
5. Page through the batch outcome API using its returned cursors. Join on `client_file_id`, not
   filename; confirm each manifest member occurs exactly once and no unknown member appears.
   Registered and already-registered outcomes must resolve to consistent document metadata.
   Count rejected/failed and unfinished outcomes separately; any unfinished outcome leaves the
   stage incomplete. Duplicate source content may legitimately share a document ID.

## Evidence to record per stage

| Measure | Value |
| --- | --- |
| Revision, workspace/tier, operator, UTC start/end | Pending user run |
| Batch ID; manifest member count | Pending |
| Total bytes; min/median/max file bytes and pages | Pending |
| Registered / already registered / rejected or failed / unfinished | Pending |
| Missing or repeated client file IDs; registry inconsistencies | Pending |
| Total duration; transfer latency p50/p95 where observable | Pending |
| Attempts, retries, lost-response resolutions; HTTP/error code counts | Pending |
| Browser heap before/peak/after; API RSS before/peak/after | Pending |
| Recovery scenario and skipped completed-file count | Pending |
| Processing enabled? Separate processing evidence reference | Pending |

Use browser performance tooling and available app process telemetry for memory; record
“unavailable” when measurement cannot be collected. Avoid saving document contents, access
tokens, or unrestricted network captures in the evidence ledger.
