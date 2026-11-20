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
- The bootstrap creates four read-only views, `<prefix>_chat_documents`, `_chat_extractions`,
  `_chat_records` and `_chat_fields`, that flatten documents and their extracted data points (the
  names are historical; see "Retired document chat" below).

## Deploying the existing dev target

Commands for a new machine and for other workspaces: [deployment guide](DEPLOYMENT_GUIDE.md).

The `dev` target in `databricks_etl/databricks.yml` holds the live dev values (catalog, warehouse,
viewer projections), so no `--var` flags are needed. The CLI profile chooses the
workspace (default `idp-mvp`; override with `PROFILE=...`).

```bash
make deploy           # make check (tests, then rebuilds frontend/dist, which the app serves), deploy, restart the App
make workspace-setup  # when migrations change: bootstrap, then grants; then deploys
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
import volume, and the import, dispatcher and exporter Jobs (CAN_MANAGE_RUN). The bootstrap runs it
as its last task, because replacing views drops their grants, and `workspace_setup` runs it again
once the App exists. A test fails when a table in `core/data_objects.py` is neither bound nor
granted.

## Deploying to another workspace

Each workspace has one settings file, `databricks_etl/targets/<name>.yml` (a copy of
`targets/example.yml.template`, included by `databricks.yml`), holding its host, catalog, schema,
table prefix, warehouse, volumes, feature switches and concurrency. Its target uses
`mode: production` with an explicit `workspace.root_path`, so names carry no `[dev user]` prefix
and the dispatcher's schedule follows `recovery_schedule_status`. (Development mode would not
override it either: per the CLI source, its pause preset only fills an empty pause status.)
`python3 scripts/deploy_workspace.py <name>` (or `make deploy-workspace TARGET=<name>`) needs only
Python and the Databricks CLI: it deploys, tolerating the first deployment's expected App error,
runs `workspace_setup`, deploys again, grants a new App, starts it and prints its address. `deployment_name` names the App (its
address) and Jobs; `--adopt-app` takes over an existing App of that name once. The
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

## Retired document chat

The "Ask documents" page (Knowledge Assistant, optionally behind a Supervisor Agent, with page
citations) was removed on `feat/remove-managed-chat` (November 2026) because both agent types are
being deprecated. A Genie space embedded in the app is planned in its place. Workspaces that ran the
chat keep what it created until someone deletes it by hand; nothing here removes it:

- the Knowledge Assistant `<deployment_name>-documents-ka` and, in supervisor mode, the Supervisor
  `<deployment_name>-chat-supervisor` (Agents UI), with their serving endpoints and experiments;
- the five UC functions `<prefix>_chat_document_fields`, `_chat_find_documents`, `_chat_invoices`,
  `_chat_invoice_totals`, `_chat_case_documents`;
- the tables `<prefix>_chat_messages` (per-user history) and `<prefix>_app_settings`.

The four `<prefix>_chat_*` views stay: they are the flattened extraction data a Genie space can use.
The code is in git history before this branch (`8efe104`).

Historical detailed instructions and evidence, including the retired chat spaces: see
[docs/archive](archive/) and [implementation handoff](archive/performance/intake-implementation-progress.md).
These archived notes contain superseded checkpoints; reconcile them with current code and live state.
