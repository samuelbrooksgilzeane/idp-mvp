# Document chat: options and feasibility gates

Research date: 30 September 2026. Decision brief, not authorization to provision resources or
start ingestion. The implementation handoff should be finalized after the user's deployment,
privacy, access and source-volume answers and the feasibility checks below.

## Repository baseline

Reviewed clean branch `feat/dark-blue-ui`, HEAD `ed8930d`. Recent changes include historical-parse
alignment and faster viewer reads (`f908d45`), stable cache-scope mounting (`89e1b3c`), citation
presentation (`0490558`), browser cache headers (`aa39f64`), Genie structured-source setup and
three-way parser/extractor concurrency. The newest deployment recorded in the detailed release
record is `01f1bc6cc5201f189195481b47d5b3aa`; the checklist's top-level deployment reference is older.
This was a code/document review, not a fresh live verification or test-suite run.

Current integration: `frontend/src/pages/AskGeniePage.tsx` embeds the configured Genie URL;
`backend/src/idp_app/api/app_config.py` supplies display configuration. There is no custom chat
service, conversation store or RAG ingestion pipeline. Retained parses already include full text,
layout, document/source hash and immutable parse identity. Viewer URLs can address historical parses.
The app currently writes PDFs under `/Volumes/workspace/idp_mvp/idp_source/incoming` through its
configured source volume. Confirm whether the new corpus includes other folders or out-of-app files.

The existing structured Genie views remain useful for exact extracted-record questions. Their
presence does not make the entire PDF corpus searchable. Preserve the existing Genie resource and
curation until a replacement is accepted; do not delete it as part of the research phase.

## Confirmed product constraints

- Volume Content Search excludes catalogs with workspace bindings. This is a documented blocker
  for the current proposed Genie-volume route, not an iframe issue.
  [Volume Content Search](https://docs.databricks.com/aws/en/volumes/content-search)
- Free Edition excludes Knowledge Assistant. It allows a limited AI Search endpoint but not Direct
  Vector Access, and provides a limited Lakebase project. Endpoint/model availability and remaining
  quotas still require checks in this workspace.
  [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)
- Knowledge Assistant can take a volume/directory as its source, build a document Q&A endpoint and
  incrementally sync sources. A sync must be triggered when files change; it is not a guaranteed
  always-on volume watcher. Its documentation does not list the same binding exclusion, but this is
  not proof that this exact catalog is accepted. Test that on an eligible workspace first.
  [Knowledge Assistant](https://docs.databricks.com/aws/en/agents/agent-bricks/knowledge-assistant)
- AI Search Delta Sync indexes consume table rows, not raw PDFs. Databricks can compute embeddings
  and manage indexing. Triggered sync avoids a continuously running synchronization pipeline.
  The published requirements do not list the volume-content-search binding exclusion; actual index
  creation/query in the selected catalog is still a required feasibility gate.
  [AI Search creation](https://docs.databricks.com/aws/en/ai-search/create-ai-search)
- The supported chat template provides a UI and can persist conversations in Lakebase/Postgres;
  persistence needs configuration. Default in-memory conversations are insufficient for this request.
  [Chat UI template](https://docs.databricks.com/aws/en/agents/custom-agents/chat-app)
- Apps support iframe embedding, but viewers still need authentication and app access. Browser
  session behavior must be tested with an ordinary user's account.
  [Embedding Apps](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/embed)
- Managed agent sessions are a separate beta alternative. Their actor identifier is a grouping
  field, not a per-user authorization boundary; the application must enforce ownership.
  [Managed sessions](https://docs.databricks.com/aws/en/agents/agent-memory/managed-sessions)

## Candidate choices

### A. Custom agent with managed AI Search — preferred candidate for the current workspace

Proposed flow:

`one configured source volume → ingestion ledger → retained parse/page-aware chunks → Delta table
→ AI Search Delta Sync index → read-only retrieval agent → authenticated chat UI + durable history`

Reuse already-successful app parses where source hashes match. This avoids paying to parse the same
file again merely to enable chat. Unparsed/new/out-of-app files require an explicitly budgeted intake
step. The index is managed, but our code owns file discovery, chunk creation, update/delete handling,
sync triggering and freshness reporting. An LLM should not autonomously decide to crawl/reindex.

Use explicit, bounded tools: search document passages, fetch a cited passage, and optionally query
allowlisted structured results for calculations. Retrieval over a few passages must not be presented
as an exhaustive sum/count across the corpus. Keep document contents as untrusted evidence, not agent
instructions. Pass only authorized, current chunks into the generation prompt.

Prefer a native chat route in the existing React/FastAPI app if seamless viewer citations and shared
navigation matter most. A separate Databricks agent/chat-template App embedded on the existing route
is an alternative if reusing the standard UI is more important. It adds deployment/auth integration
and consumes another app slot. The current frontend is Vite React, not the template's Next.js stack;
reuse components or isolate the template, rather than replacing the current application wholesale.

Persistent history: prefer a proven Lakebase/Postgres configuration if available. Select managed
sessions only after confirming feature availability and ownership enforcement. Do not silently use
local app disk or browser storage as the durable source of truth.

### B. Knowledge Assistant plus chat UI — strongest managed-ingestion candidate on paid Databricks

First validate the source volume on an eligible workspace, required permissions, endpoint access,
model/region support, incremental sync and deletion propagation. Then integrate the agent endpoint
with the supported chat UI and persistent history. A volume connection alone is not a finished,
embedded chat experience. This route is excluded if the deployment must remain Free Edition.

### C. External hosted RAG — conditional fallback

Only investigate a specific vendor once external data processing and budget are allowed. It would
need a supported ingestion API plus our connector from the source volume, identity mapping, saved
history, citation mapping and verified update/delete propagation. A hosted chatbot cannot be assumed
to mount a Databricks volume. This option shifts operations elsewhere but does not remove source sync.

## Feasibility gates before implementation commitment

1. Confirm target workspace/edition/region, fixed volume/path, expected file/page count and update rate,
   whether uploads bypass the app, and private versus shared document/chat access.
2. Inspect existing AI Search capacity, eligible embedding/chat models, app permissions and chat-store
   availability with metadata-only reads. Inspect catalog bindings; do not change/unbind them.
3. After agreement on a tiny test budget, prove one index from a few synthetic text rows in the exact
   target catalog, a retrieval query and one model response. This must not ingest the real volume.
4. Verify index updates and deletion, stable citation metadata and app-identity access. A successful
   endpoint-list API alone does not pass this gate. Record quota, permissions and unsupported-feature
   errors distinctly; stop instead of falling into repeated retries.
5. Prove durable conversation resume after app restart, using a synthetic conversation; verify another
   user cannot list/read/change it by guessing identifiers. Coordinate any restart with the user.
6. If using an iframe, verify login, streaming, reload and citation navigation inside the existing app.
   Do not make iframe support the dependency for the underlying retrieval service.
7. Only after these pass, finalize the execution plan and approve bounded real-document ingestion.

## Required implementation contracts once a route is selected

- Server-owned source-volume configuration; never accept arbitrary paths from the browser/model.
- Ingestion ledger records document ID/path, source fingerprint, parse ID, chunker/model version,
  index generation, ingestion state and last successful sync. Stable chunk IDs make retries idempotent.
- Page-aware chunks carry document/parse/page identifiers and source locations for the existing viewer.
  For files uploaded outside the app, define registration and viewer eligibility explicitly.
- A complete successful scan is required before inferring file removal. Partial listing failures must
  never cause mass tombstones. Failed/replaced versions cannot become visible as current content.
- Mark deleted/revoked sources unavailable immediately in retrieval authorization; propagate index
  removals separately. Stale search hits are filtered before model context construction. Specify how
  previously saved messages/citations behave after source deletion; index deletion cannot erase history.
- Persist users, conversations and ordered messages with idempotency and server-derived identity.
  Enforce ownership in every list/get/append/rename/delete operation. Store user-visible evidence and
  tool summaries as required; do not depend on hidden model reasoning for conversation replay.
- Bound ingestion batches, model tokens, retrieval top-k, file sizes, retries and concurrent work.
  Do not put full-volume scanning or indexing on the HTTP request path or app startup.
- Distinguish uploaded, prepared, indexed, failed and pending deletion; show last successful sync.
- Start with manual/triggered synchronization; scheduled processing needs explicit budget approval.
- Retain existing parse/extract/export behavior, Genie curation, and current viewer/citation fixes.
  Account for the existing app's resource-binding limit and explicit grants.
- Test small synthetic fixtures locally first: repeated sync, lost response, changed file, deletion,
  skipped file, malformed input, inaccessible citation, prompt injection and cross-user isolation.

## Decisions requested

Deployment budget/edition; whether data may leave Databricks; document visibility and conversation
privacy; exact volume/path and upload routes; passage Q&A versus exact structured calculations.
Before the final plan also establish corpus size, sync-latency expectations, language/file types,
chat retention and who will use the app. No runtime code or remote resources changed by this brief.
