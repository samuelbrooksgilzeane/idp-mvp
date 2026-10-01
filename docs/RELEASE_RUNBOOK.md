# Release smoke test and operating runbook

Prepared 29 September 2026 from repository code. This is a procedure, not evidence that
live acceptance gates passed. Record live results in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
Use [DEPLOYMENT_NOTES.md](DEPLOYMENT_NOTES.md) for deployment prerequisites.

## Budget and starting conditions

The user performs document uploads and real-document parsing/extraction. The agent validates
code with local synthetic fixtures and inspects the user's existing outcomes. Do not upload
agent test PDFs, run a capacity batch, repeat inference for navigation benchmarks, or enable
indexing/scheduled recovery as part of this smoke procedure. Stop on quota/service-limit errors;
record the error rather than repeatedly retrying. The configured 1,000-file ceiling is not a
validated capacity claim or a promise of available Free Edition allowance.

Before testing, record the workspace, app URL/deployment ID, release snapshot, target
catalog/schema/prefix, resource IDs and authenticated identity. Confirm health, required
migrations/grants and actual deployed settings. Authenticated `/api/upload-batches/limits`
reports upload limits and automatic preparation/bulk extraction/export flags;
`/api/app-config` reports whether document chat is enabled (`chat_enabled`). Viewer projections and recovery schedule
state also require deployment/resource inspection; do not infer them from a visible page.

- `IDP_AUTO_PREPARE_ENABLED` permits upload-triggered background preparation; uploads may consume
  parsing resources when enabled. Leave off until the intended workload is approved.
- `IDP_BULK_EXTRACTION_ENABLED` exposes durable extraction; submitting extraction starts work.
- `IDP_BULK_EXPORT_ENABLED` exposes durable exports; submitting an export starts its Job.
- `IDP_VIEWER_PROJECTION_ENABLED` selects retained viewer data; enabling it requires its migration.
- Dispatcher recovery schedule activation is separate from app startup. Keep it paused until
  the small workflow passes and the user approves its workload.

## User smoke procedure: two unique PDFs and one duplicate

Use one short representative multi-page PDF, one tiny disposable PDF, and a byte-identical
copy of the first PDF under another filename. Reuse existing appropriate fixtures where
possible. Give the batch a unique case label. Keep a note of document/batch/run IDs and times;
never put document contents or credentials into release evidence.

| Step | User action | Expected outcome and agent verification |
| --- | --- | --- |
| 1 | Open documents, results and schemas; refresh each once. | Authenticated pages load without empty/error states being confused with successful data loads. Existing project data remains visible. Record first and repeat timings separately. |
| 2 | Upload the two unique PDFs and duplicate in one small batch. | Each selection has an explicit outcome. Two unique accepted documents and an `Already uploaded` duplicate outcome; no third active registry entry for the same bytes. Agent checks each accepted registry row, hash and source-volume object. If preparation is off, uploads remain available for manual preparation. |
| 3 | While an unfinished transfer is visible, pause; then retry unfinished files. If the tiny batch finishes too quickly, mark this live step unexercised. | Active transfers may finish before pause takes effect. Completed members are skipped. No extra documents are required merely to force this condition. |
| 4 | Refresh during an unfinished upload if feasible; reselect the original files and resume. | Saved outcomes survive. Browser file handles do not survive refresh, so reselection is required. Completed files are skipped; ambiguous transfer outcomes reconcile to the same registered document. |
| 5 | Within the agreed inference budget, prepare the representative document and extract using a published schema. | Progress reaches a terminal success or an explicit error. Viewer page count/navigation match the PDF; selecting a citation highlights the appropriate page/evidence. Agent inspects parse ID, source hash, extraction ID, schema version/hash and citation consistency. |
| 6 | Refresh during processing or navigate away and back. | Durable batch status remains retrievable. Dismissing progress does not cancel submitted work. If an actual terminal failure exists, use `Retry failed documents`; agent confirms successful members were not resubmitted. Do not manufacture a live inference failure. |
| 7 | Review results and download one export. | Values/rows match the selected retained runs and schema. If exporting a partial batch, the UI requests completed-only confirmation; historical duplicate runs require explicit confirmation. Download succeeds before expiry. |
| 8 | Reopen an existing historical result after a newer result exists, if already available. | Historical result retains its own parse and schema identity. Do not create another inference run solely for this check; use local regression evidence if no live historical pair exists. |
| 9 | Delete the designated disposable PDF after its processing is idle. | It disappears from active document views and source access fails. Agent verifies source removal and deleted registry state. This does not purge retained results, page images or exports. |

If the UI differs from these outcomes, capture the route, time, sanitized error code and affected
IDs. Add a checklist issue with expected versus actual behavior; avoid repeatedly clicking
submission buttons or uploading replacement copies as a workaround.

## Start, stop and recovery

Use the existing app's Databricks start/stop controls. Coordinate the idle stop/start check
with the user, record durable batch/result IDs first, stop the app only when their interactive
test is idle, then start the same deployment. Verify authenticated health, unchanged deployment
identity, retained documents/results and the same durable status IDs. Redeployment is not
required to preserve persisted state. Browser-local progress may depend on the same browser
and identity; registry/work records are the durable source of truth.

Stopping the app does **not** stop independent Jobs, schedules or the SQL warehouse. For a
quota incident, inspect active project Jobs and the recovery schedule separately. Pause
scheduled recovery and avoid new submissions while diagnosing; coordinate cancellation of
active user work. Do not reset durable tables or remove leases to force a retry.

Upload recovery uses saved per-file states; `Retry unfinished files` skips registered members.
A rejected PDF or exhausted attempts needs its explicit error investigated. Dispatcher recovery
uses durable claims/leases and checks active Job status before reclaiming expired work. A stale
looking row is not proof that its Job stopped. Inspect Job/run IDs, attempts and claim expiry;
allow the configured recovery mechanism to reconcile after prerequisites/budget are restored.

Extraction `Retry submission` resolves an ambiguous original submission. Terminal failed-member
retry is a separate operation and must preserve successful members and original pinned inputs.
Export `Retry submission` replays saved intent using the existing export request identity;
do not create many replacement requests after a submission timeout. An expired/missing export
can be regenerated from retained successful results without repeating document inference.

## Retention and support

| Data | Current behavior | Cleanup responsibility |
| --- | --- | --- |
| Source PDFs | Stored in source volume; UI deletion removes the file before marking registry deleted. These operations are not atomic. | Project maintainer investigates partial deletion. A storage error leaves registry intact; a registry error can leave an active row whose file is gone. Reconcile the exact document before retrying; do not bulk-delete rows. |
| Registry, parse/extraction results, work/batch records | Durable retained data; app stop/start and code rollback do not purge it. | User must confirm retention policy and name a maintainer before release sign-off. Preserve historical provenance. |
| Page images/viewer artifacts | Retained separately in artifacts volume; deleting a source is not a full purge. | Maintainer performs separately approved scoped cleanup after checking references. |
| Durable export artifacts | API expiry is calculated from request creation, default 24 hours (`IDP_EXPORT_RETENTION_HOURS`, allowed 1–168). Expired downloads return 410. | Expiry prevents API download; no scheduled artifact purge is established by this implementation. Maintainer owns physical cleanup of expired artifacts and orphan reconciliation. Never describe expiry as guaranteed file deletion. |
| Chat/index copies | Source deletion does not establish immediate index removal. | Designated chat maintainer owns supported sync, freshness checks and deletion verification. |

Support owner and escalation channel: **to be assigned by the user before release acceptance**.
For support, record deployment ID/snapshot, sanitized feature flags, timestamp, route, request/
batch/document/run/export IDs, error code, Job state and last successful operation. Keep tokens,
raw PDFs, SQL credentials and client document content out of logs shared for triage.

Batch/export ownership restrictions are distinct from project document visibility: uploader
identity is audit metadata, not per-user document isolation. An ordinary colleague must verify
app, documents, viewer, schemas, exports and document chat with their own identity. Do not impersonate
them or infer colleague access from the deployment service principal's successful test.

## Rollback and repeatable demonstration

Before deployment retain the previous known-good release snapshot, app deployment ID,
resolved non-secret bundle configuration and resource IDs. Record local check results with the candidate snapshot. Use the same target
and existing resource identities; preserve migrations and persisted data.

For code rollback, prepare the known-good snapshot in an isolated checkout, restore the
reviewed target configuration, inspect the bundle
plan, and deploy that snapshot through the same bundle flow. Verify authenticated health,
frontend assets, feature flags and access to retained results. Confirm the old code understands
current additive schema before rolling back. Do not use `bundle destroy`, drop tables/volumes,
or deploy a stale per-workspace overlay as rollback. Code rollback cannot undo inference costs,
source deletion, prior writes or external index changes. If compatibility is uncertain, stop
new submissions and apply a forward fix rather than mutate retained data.

A repeatable demo uses already retained results: open the document list, inspect the multi-page
viewer and citations, open its published schema, review the saved extraction and download an
unexpired export. Record the deployment ID and regression evidence alongside the demo. Leave
upload/inference optional and user-operated so repeated demos do not multiply consumption.

## Document chat handoff

Document chat is the app's "Ask documents" page, enabled by the `chat_endpoint` bundle variable.
Its sources and rollout are tracked in [the implementation plan](planning/IMPLEMENTATION_PLAN.md).
App selections do not filter chat questions. Verify scope and intended colleague grants before
connecting a document source, and test the link with the ordinary colleague account.

For an approved document-search test, reuse the disposable user fixture with a distinctive
non-sensitive phrase: upload → record source ID/time → request supported sync → record sync
completion/freshness → ask about it and inspect its cited source. Then delete the source in the
app, verify volume/registry outcomes, sync again, and confirm the source is absent from subsequent
answers. Record observed lag and failed syncs; do not assume continuous synchronization or
immediate removal. The agent inspects these outcomes after the user-operated test.

Sync owner and cadence: **user to confirm**. Communicate the last verified sync time and known
coverage in the release handoff. Until configured and verified, tell pilot users: **the chat
cannot answer from PDF content yet.**

## Evidence boundary

This runbook prepares checklist sections 2, 5 and 6. Live stop/start, deletion reconciliation,
colleague access, inference consistency, export expiry and search/sync verification remain
unchecked until their actual observations are recorded. Local synthetic tests support code
validation but do not establish deployed permissions, available quota or live capacity.
