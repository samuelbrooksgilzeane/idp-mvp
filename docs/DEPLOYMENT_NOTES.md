# Deployment notes

Updated: 30 September 2026. Release gates live in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

- Publish through the bundle to resolve target-specific environment values and resource IDs.
  Standalone `app.yaml` contains development-specific defaults and is not the production configuration.
- New workspace deployments need their own resources, grants and deployment state.
- **Retired chat space (dev only):** a checkout that still has a gitignored
  `databricks_etl/resources/*.generated.yml` overlay from the retired question-answering space
  includes it in the bundle, and deploying without it later deletes that space. Before the next dev
  deploy, unbind the resource or delete the space deliberately; see the Caution in
  [the implementation plan](planning/IMPLEMENTATION_PLAN.md).
- Review the bundle plan before applying migrations/resources. Creating Job definitions does not
  execute migrations. Verify grants again after replacing views; earlier replacements revoked grants.
- Bulk intake needs upload-batch tables even when automatic preparation is off. Queue, bulk extraction,
  bulk export and viewer projections each require their own prerequisites and explicit activation.
- Viewer projections: once enabled, the parser Job writes one for every successful parse. Parses from
  before that have none; the viewer still works from the retained parse but opens them slowly. Backfill
  them with `databricks_etl/src/backfill_viewer_projection.py` (at most 25 `--parse-run-id` per run,
  no inference). Standalone `app.yaml` does not enable projections, another reason to deploy through the bundle.
- Keep recovery schedules paused until smoke verification. Start/stop of the app is independent of Jobs,
  warehouses and schedules; it is not a shutdown mechanism for all project compute.
- The source volume contains uploaded PDFs. App deletion removes the PDF then marks its registry row
  deleted; it does not purge retained results or generated artifacts. Direct SQL deletion is different.
- Document chat is the app's "Ask documents" page, shown unless the bundle variable `chat_mode` is
  `off`. The bootstrap creates read-only `<prefix>_chat_*` views for its SQL functions.

## Deploying the existing dev target

Commands for a new machine and for other workspaces: [deployment guide](DEPLOYMENT_GUIDE.md).

The `dev` target in `databricks_etl/databricks.yml` holds the live dev values (catalog, warehouse,
viewer projections, chat endpoint), so no `--var` flags are needed. The CLI profile chooses the
workspace (default `idp-mvp`; override with `PROFILE=...`).

```bash
make deploy           # make check (tests, then rebuilds frontend/dist, which the app serves), deploy, restart the App
make workspace-setup  # when migrations or chat settings change: bootstrap, chat provisioning, grants; then deploys
make bootstrap        # repair: the bootstrap alone (it re-applies the App's direct grants), then deploys
make grants           # repair: the App's direct grants alone; additive and safe to repeat
```

Parse/extract parallelism is set by the bundle variables `parse_concurrency`, `extraction_concurrency`
(default 3 each) and `combined_inference_concurrency` (default 6; must be at least their sum). It
applies within one multi-document Job run.

**Why there is a grants Job.** A Databricks App binds at most 20 resources and this App needs more, so
the bundle cannot grant everything through the App. The `app_access_grants` Job
(`databricks_etl/src/grant_app_access.py`) grants the rest as the deploying identity: the viewer
projection tables, the upload, folder-import and work-queue tables, invoice line candidates, the
`app_settings` table, the import volume, the import, dispatcher and exporter Jobs (CAN_MANAGE_RUN),
the chat views, functions and history table, and, unless `chat_mode` is `off`, the chat agents:
the Knowledge Assistant (CAN_MANAGE, for Sync), its serving endpoint and experiment, and when chat
goes to a Supervisor, the Supervisor, its experiment and its sub-agents' endpoints. The bootstrap
runs it as its last task, because replacing views drops their grants, and `workspace_setup` runs it
again after chat provisioning. A test fails when a table in `core/data_objects.py` is neither bound
nor granted.

## Deploying to another workspace

Each workspace has one settings file, `databricks_etl/targets/<name>.yml` (a copy of
`targets/example.yml.template`, included by `databricks.yml`), holding its host, catalog, schema,
table prefix, warehouse, volumes, `chat_mode`, feature switches and concurrency. Its target uses
`mode: production` with an explicit `workspace.root_path`, so names carry no `[dev user]` prefix
and the dispatcher's schedule follows `recovery_schedule_status`. (Development mode would not
override it either: per the CLI source, its pause preset only fills an empty pause status.)
`python3 scripts/deploy_workspace.py <name>` (or `make deploy-workspace TARGET=<name>`) needs only
Python and the Databricks CLI: it deploys, tolerating the first deployment's expected App error,
runs `workspace_setup`, deploys again, grants a new App, starts it and prints its address. `deployment_name` names the App (its
address), Jobs and chat agents; `--adopt-app` takes over an existing App of that name once. The
grants Job also gives `app_users_group` (default `users`, everyone) CAN_USE on the App.
`make check` fails when `frontend/dist` differs from a fresh build, because CLI-only deploys ship
the committed build. The full steps are in the [deployment guide](DEPLOYMENT_GUIDE.md).

Who may drop folders in for import stays a manual grant, because it is an access decision (guide,
step 3.4).

Folder import in use: copy a folder of PDFs with `databricks fs cp -r ./invoices
dbfs:/Volumes/$CATALOG/$SCHEMA/idp_import/invoices -p PROFILE` (or upload it in Catalog Explorer), then
choose **Import from folder** on the Documents page. The import Job registers each PDF exactly as a
browser upload would (same dedupe, same outcomes, same automatic preparation). It never deletes from
the import folder: a path names whatever file is there now, which may be a different file than the
one registered, and the volume cannot delete conditionally. Remove a folder once the app shows its
files registered; importing it again is harmless (each file resolves to its existing document).
Failed files show their reason in the app. **Retry unfinished files** starts another run;
re-running is safe. Only the App and Jobs write `idp_source`.
Anyone with WRITE VOLUME on `idp_import` can also read or delete other users' pending files there.

## Document chat: Knowledge Assistant and Supervisor

The bundle variable `chat_mode` chooses the chat: `knowledge_assistant` (default: fast, answers
with page citations), `supervisor` (the KA plus SQL tools for totals and extracted values; slower)
or `off`. The `workspace_setup` Job's `provision_chat` task (`databricks_etl/src/provision_chat.py`)
creates or updates, by name, the Knowledge Assistant `<deployment_name>-documents-ka` (files source:
the source volume's `incoming/` folder) and, in `supervisor` mode, the Supervisor
`<deployment_name>-chat-supervisor` with the KA and, unless `supervisor_functions_enabled` is
`"false"`, the five read-only UC functions the bootstrap creates (`<prefix>_chat_document_fields`,
`_chat_find_documents`, `_chat_invoices`, `_chat_invoice_totals`, `_chat_case_documents`). It is
idempotent and never deletes. It requests one KA Sync, so documents uploaded before chat existed
are indexed, and records the KA id and the chat's serving endpoint (names Databricks generates) in
`<prefix>_app_settings`; the App reads them from there (cached five minutes), so no deploy variable
or redeploy is needed after provisioning. If Agent Bricks is off in the workspace, the task fails
with a message naming the admin setting. Agent Bricks APIs are Beta.

`chat_endpoint` (blank by default) overrides the provisioned endpoint: chat then goes to exactly
that serving endpoint (a Supervisor, another agent or a specific KA), and the grants Job gives the
App CAN_QUERY on it, plus the Supervisor and sub-agent permissions when it is a Supervisor. The
`dev` target uses it for the Free Edition Supervisor from gate G7. With `ka_sync_enabled` (default
`"true"`), uploads, imports and deletions request a coalesced KA Sync.

```bash
make workspace-setup   # provisions chat, grants it and deploys; repeat after changing chat_mode
```

Knowledge Assistant answers carry numbered citations: a marker after the text each one supports
and a numbered Sources list. A citation links only when it points at this workspace's source
volume (`.../incoming/<document_id>.pdf#page=N` plus the passage); it opens a new tab,
`/sources/<document_id>`, at the cited page with the passage highlighted by matching it to the
page's parsed text. Every page stays reachable there, and users need no access to the files. Other
citations stay plain labels.

The App calls the chat endpoint itself, keeps per-user history in `<prefix>_chat_messages`
(created by the bootstrap's `migrate_chat_history` task) and shows citations and extracted values as
plain text, with `<document_id>.pdf` rewritten to the original file name. No separate chat app or
Lakebase is needed. Free Edition allows one Supervisor Agent and its KA indexes but cannot answer
(G7).

Answers can take up to a minute when several tools run.

**Finding the trace behind a user's conversation.** Every answer stores its MLflow trace id in the
history row (never shown to users), and the trace is tagged `idp.user` and `idp.conversation_id`.
Either start from the history:

```sql
SELECT conversation_id, seq, created_at,
       get_json_object(payload, '$.text')     AS answer,
       get_json_object(payload, '$.trace_id') AS trace_id
FROM <catalog>.<schema>.<prefix>_chat_messages
WHERE user_id = 'ann@example.com' AND role = 'assistant'
ORDER BY created_at DESC;
```

then open the Supervisor's experiment, **Traces**, and search for the `tr-...` id; or start in the
Traces tab and filter with ``tags.`idp.user` = 'ann@example.com'`` (or
``tags.`idp.conversation_id` = '<id>'``). Tagging is best effort: without CAN_EDIT on the
experiment the App logs `Chat trace tagging failed`, and the stored trace id still links the two.
Answers given before this change have no trace id.

Historical detailed instructions and evidence, including the retired chat space: see
[docs/archive](archive/) and [implementation handoff](archive/performance/intake-implementation-progress.md).
These archived notes contain superseded checkpoints; reconcile them with current code and live state.
