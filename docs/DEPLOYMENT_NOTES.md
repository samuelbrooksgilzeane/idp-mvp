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

The bundle's required variables have no defaults and are not stored in the repository. The values
below match the live `dev` deployment (non-secret). Omitting `viewer_projection_enabled=true` would
turn viewer projections off, so always pass the full set and review the plan.

```bash
make check
cd databricks_etl
VARS=(--var catalog=workspace --var project_schema=idp_mvp --var source_volume_name=idp_source
      --var artifacts_volume_name=idp_artifacts --var warehouse_id=647704f77f24020a
      --var viewer_projection_enabled=true)
databricks bundle plan   -t dev -p idp-mvp "${VARS[@]}"
databricks bundle deploy -t dev -p idp-mvp "${VARS[@]}"
databricks bundle run    -t dev -p idp-mvp "${VARS[@]}" idp_app   # activates the new app code
```

`make check` rebuilds `frontend/dist`, which is what the app serves. Parse/extract parallelism is set by
the bundle variables `parse_concurrency`, `extraction_concurrency` (default 3 each) and
`combined_inference_concurrency` (default 6; must be at least their sum). It applies within one
multi-document Job run.

## Deploying to another workspace

The bundle has no fixed workspace host; the CLI profile chooses the workspace. A new workspace needs its
own tables, grants and deployment state, so the first deployment runs in the order below. Deploy from a
checkout of the release branch after `make setup`; the build in `frontend/dist` is what the app serves.

Prerequisites: an existing Unity Catalog catalog you may create a schema in, a SQL warehouse, serverless
Jobs, and AI Functions (`ai_parse_document`, `ai_extract`) available in the workspace's region.

```bash
# Sign in once, then set this workspace's values
databricks auth login --host https://YOUR-WORKSPACE --profile NEW_PROFILE
PROFILE=NEW_PROFILE
TARGET=dev        # app idp-mvp-dev, tables idp_dev_*; the prod target uses PREFIX=idp and production mode
PREFIX=idp_dev
CATALOG=YOUR_CATALOG
SCHEMA=idp_mvp
WAREHOUSE=YOUR_WAREHOUSE_ID

make check                                          # tests, then rebuilds frontend/dist
ls databricks_etl/resources/*.generated.yml 2>/dev/null && echo "remove per-workspace overlays first"
cd databricks_etl
VARS=(--var catalog=$CATALOG --var project_schema=$SCHEMA --var source_volume_name=idp_source
      --var artifacts_volume_name=idp_artifacts --var warehouse_id=$WAREHOUSE
      --var viewer_projection_enabled=true)
sql() {  # run one statement on the warehouse and print its state
  python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s"}))' "$WAREHOUSE" "$1" |
    databricks api post /api/2.0/sql/statements -p "$PROFILE" --json @/dev/stdin |
    python3 -c 'import json,sys; s=json.load(sys.stdin)["status"]; print(s["state"], s.get("error", {}).get("message", ""))'
}

# 1. Creates the Jobs. On a first deployment the App step fails: its grants name tables that the
#    bootstrap only creates in step 2.
databricks bundle plan   -t $TARGET -p $PROFILE "${VARS[@]}"
databricks bundle deploy -t $TARGET -p $PROFILE "${VARS[@]}"
# 2. Schema, volumes, tables, views and the published extraction schemas
databricks bundle run    -t $TARGET -p $PROFILE "${VARS[@]}" governed_data_bootstrap
# 3. Viewer projection tables (databricks_etl/sql/migrate_viewer_projection.sql; not in the bootstrap)
sql "CREATE TABLE IF NOT EXISTS $CATALOG.$SCHEMA.${PREFIX}_parsed_page_manifest (parse_run_id STRING NOT NULL, payload STRING NOT NULL) USING DELTA"
sql "CREATE TABLE IF NOT EXISTS $CATALOG.$SCHEMA.${PREFIX}_parsed_page_elements (parse_run_id STRING NOT NULL, page_id INT NOT NULL, payload STRING NOT NULL) USING DELTA"
# 4. Deploy again, which applies the App's table and volume grants, then start the App
databricks bundle plan   -t $TARGET -p $PROFILE "${VARS[@]}"
databricks bundle deploy -t $TARGET -p $PROFILE "${VARS[@]}"
databricks bundle run    -t $TARGET -p $PROFILE "${VARS[@]}" idp_app
# 5. The App reads projections outside its 20 bound resources, so grant them directly
APP_SP=$(databricks apps get idp-mvp-$TARGET -p $PROFILE -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["service_principal_client_id"])')
sql "GRANT SELECT ON TABLE $CATALOG.$SCHEMA.${PREFIX}_parsed_page_manifest TO \`$APP_SP\`"
sql "GRANT SELECT ON TABLE $CATALOG.$SCHEMA.${PREFIX}_parsed_page_elements TO \`$APP_SP\`"
# 6. Folder import, also outside the 20 bindings: the App lists the import volume and starts the
#    import Job. The Job itself runs as the deploying identity, which owns the volumes and tables.
sql "GRANT READ VOLUME ON VOLUME $CATALOG.$SCHEMA.idp_import TO \`$APP_SP\`"
# Dev-mode deploys prefix job names with "[dev <user>] ", so match the name suffix.
IMPORT_JOB=$(databricks jobs list -p $PROFILE -o json | python3 -c 'import json,sys; print(next(j["job_id"] for j in json.load(sys.stdin) if j["settings"]["name"].endswith("idp-mvp-'$TARGET'-folder-importer")))')
databricks jobs update-permissions $IMPORT_JOB -p $PROFILE --json "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP\", \"permission_level\": \"CAN_MANAGE_RUN\"}]}"
# 7. Who may drop folders in: users need to write into the import volume (and to see it).
USERS_GROUP='account users'   # or the workspace's all-users group
sql "GRANT USE CATALOG ON CATALOG $CATALOG TO \`$USERS_GROUP\`"
sql "GRANT USE SCHEMA ON SCHEMA $CATALOG.$SCHEMA TO \`$USERS_GROUP\`"
sql "GRANT READ VOLUME, WRITE VOLUME ON VOLUME $CATALOG.$SCHEMA.idp_import TO \`$USERS_GROUP\`"
```

Folder import in use: copy a folder of PDFs with `databricks fs cp -r ./invoices
dbfs:/Volumes/$CATALOG/$SCHEMA/idp_import/invoices -p PROFILE` (or upload it in Catalog Explorer), then
choose **Import from folder** on the Documents page. The import Job registers each PDF exactly as a
browser upload would (same dedupe, same outcomes, same automatic preparation) and deletes each file
once it is registered; failed files stay in the folder with their reason in the app. **Retry
unfinished files** starts another run; re-running is safe. Only the App and Jobs write `idp_source`.
Anyone with WRITE VOLUME on `idp_import` can also read or delete other users' pending files there.

Every later deployment to that workspace is step 4's plan, deploy and run. If the bootstrap is run again, deploy afterwards: replacing
views drops the grants the App binding applied.

## Document chat: Knowledge Assistant and Supervisor

The bootstrap creates five read-only UC functions (`<prefix>_chat_document_fields`,
`_chat_find_documents`, `_chat_invoices`, `_chat_invoice_totals`, `_chat_case_documents`) after the
chat views. `scripts/provision_chat.py` then creates or updates, by name, the Knowledge Assistant
`<app>-<target>-documents-ka` (files source: the source volume's `incoming/` folder) and the
Supervisor `<app>-<target>-chat-supervisor` (the KA plus the five functions as tools). It is
idempotent, never deletes, and prints the KA id and both serving endpoint names, which Databricks
generates. Agent Bricks APIs are Beta.

```bash
# After the bootstrap and deploy above. Read-only preview first.
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
The App is at the 20-binding cap, so these grants are direct (`*_ID` from the script output):

```bash
# The App queries the Supervisor, which runs the chat functions (and the KA, where present).
# Agent permissions do not reach the serving endpoints: without CAN_QUERY on each endpoint (the
# Supervisor's and the KA's) every question fails with PERMISSION_DENIED.
databricks supervisor-agents update-permissions $SUPERVISOR_ID -p $PROFILE --json \
  "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP\", \"permission_level\": \"CAN_QUERY\"}]}"
for ENDPOINT in $SUPERVISOR_ENDPOINT $KA_ENDPOINT; do   # omit $KA_ENDPOINT without a KA
  EID=$(databricks serving-endpoints get $ENDPOINT -p $PROFILE -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  databricks serving-endpoints update-permissions $EID -p $PROFILE --json \
    "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP\", \"permission_level\": \"CAN_QUERY\"}]}"
done
for f in document_fields find_documents invoices invoice_totals case_documents; do
  sql "GRANT EXECUTE ON FUNCTION $CATALOG.$SCHEMA.${PREFIX}_chat_$f TO \`$APP_SP\`"
done
for v in documents extractions fields records; do
  sql "GRANT SELECT ON TABLE $CATALOG.$SCHEMA.${PREFIX}_chat_$v TO \`$APP_SP\`"
done
sql "GRANT SELECT, MODIFY ON TABLE $CATALOG.$SCHEMA.${PREFIX}_chat_messages TO \`$APP_SP\`"
# Trace tagging: the App labels each answer's MLflow trace with the user and conversation.
# $SUPERVISOR_EXPERIMENT_ID is "experiments.supervisor" in the provisioning output.
databricks experiments update-permissions $SUPERVISOR_EXPERIMENT_ID -p $PROFILE --json \
  "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP\", \"permission_level\": \"CAN_EDIT\"}]}"
# Where a KA exists: CAN_QUERY for the App (the Supervisor calls it), CAN_MANAGE for automatic Sync.
databricks knowledge-assistants update-permissions $KA_ID -p $PROFILE --json \
  "{\"access_control_list\": [{\"service_principal_name\": \"$APP_SP\", \"permission_level\": \"CAN_MANAGE\"}]}"
# Deploy with the endpoint (and, where a KA exists, automatic Sync), then restart the App:
#   --var chat_endpoint=<supervisor endpoint> [--var ka_sync_enabled=true --var ka_id=$KA_ID]
```

Re-running the bootstrap replaces the chat functions and views, which drops their grants: deploy and
re-apply the grants above afterwards. Answers can take up to a minute when several tools run.

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
