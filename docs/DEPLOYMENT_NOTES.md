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
- Document chat is the app's "Ask documents" page, shown when the bundle variable `chat_endpoint`
  is set. The bootstrap creates read-only `<prefix>_chat_*` views for its SQL functions.

## Deploying the existing dev target

Commands for a new machine and for other workspaces: [deployment guide](DEPLOYMENT_GUIDE.md).

The `dev` target in `databricks_etl/databricks.yml` holds the live dev values (catalog, warehouse,
viewer projections, chat endpoint), so no `--var` flags are needed. The CLI profile chooses the
workspace (default `idp-mvp`; override with `PROFILE=...`).

```bash
make deploy        # make check (tests, then rebuilds frontend/dist, which the app serves), deploy, restart the App
make bootstrap     # only when migrations change: runs the bootstrap (which re-applies the App's direct grants), then deploys
make grants        # re-applies the App's direct grants on their own; additive and safe to repeat
```

Parse/extract parallelism is set by the bundle variables `parse_concurrency`, `extraction_concurrency`
(default 3 each) and `combined_inference_concurrency` (default 6; must be at least their sum). It
applies within one multi-document Job run.

**Why there is a grants Job.** A Databricks App binds at most 20 resources and this App needs more, so
the bundle cannot grant everything through the App. The `app_access_grants` Job
(`databricks_etl/src/grant_app_access.py`) grants the rest as the deploying identity: the viewer
projection tables, the import volume, the import Job (CAN_MANAGE_RUN), the chat views, functions and
history table, and, when `chat_endpoint` is set, the Supervisor, its Knowledge Assistant, both
serving endpoints and the Supervisor's experiment (found from the endpoint name). The bootstrap runs
it as its last task, because replacing views drops their grants.

## Deploying to another workspace

The bundle has no fixed workspace host; the CLI profile chooses the workspace. A new workspace needs
its own tables, grants and deployment state: `make deploy-first` deploys (the App step fails the
first time: its bound grants name tables the bootstrap creates), runs the bootstrap, deploys again,
runs the grants Job and starts the App. Because the `dev` target holds the original dev workspace's
values, every command for another workspace passes that workspace's values in `BUNDLE_VARS`,
including `chat_endpoint` (blank until its agents exist). The full commands, from a new machine to
chat, are in the [deployment guide](DEPLOYMENT_GUIDE.md). For prod use `TARGET=prod` (tables
`idp_*`, production mode).

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

The bootstrap creates five read-only UC functions (`<prefix>_chat_document_fields`,
`_chat_find_documents`, `_chat_invoices`, `_chat_invoice_totals`, `_chat_case_documents`) after the
chat views. `scripts/provision_chat.py` then creates or updates, by name, the Knowledge Assistant
`<app>-<target>-documents-ka` (files source: the source volume's `incoming/` folder) and the
Supervisor `<app>-<target>-chat-supervisor` (the KA plus the five functions as tools). It is
idempotent, never deletes, and prints both serving endpoint names, which Databricks generates.
Agent Bricks APIs are Beta.

```bash
# After the first deployment above. Read-only preview first.
PROFILE=idp-mvp; TARGET=dev; CATALOG=workspace; SCHEMA=idp_mvp
cd scripts
uv run --project ../backend python provision_chat.py --host https://<workspace-host> --profile $PROFILE \
  --target $TARGET --catalog $CATALOG --project-schema $SCHEMA --dry-run
uv run --project ../backend python provision_chat.py --host https://<workspace-host> --profile $PROFILE \
  --target $TARGET --catalog $CATALOG --project-schema $SCHEMA
# Free Edition allows ONE Supervisor Agent (delete the G7 probe first) and its KA indexes but
# cannot answer (G7), so provision the Supervisor alone:
#   ... --no-knowledge-assistant
```

Then turn the chat page on in the IDP App (**Ask documents** in the sidebar). The App calls the
Supervisor itself, keeps per-user history in `<prefix>_chat_messages` (created by the bootstrap's
`migrate_chat_history` task) and shows citations and extracted values as plain text, with
`<document_id>.pdf` rewritten to the original file name. No separate chat app or Lakebase is needed.
Set the Supervisor's endpoint (and, for automatic KA Sync, the KA id) and deploy; the grants Job
then gives the App everything chat needs:

```bash
# dev: edit chat_endpoint (and optionally ka_sync_enabled: "true", ka_id) under targets.dev in
# databricks_etl/databricks.yml; another workspace: add them to BUNDLE_VARS
make deploy
make grants
```

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
