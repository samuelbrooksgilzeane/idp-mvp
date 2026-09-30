# Deployment notes

Updated: 30 September 2026. Release gates live in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

- Publish through the bundle to resolve target-specific environment values and resource IDs.
  Standalone `app.yaml` contains development-specific defaults and is not the production configuration.
- New workspace deployments need their own resources, grants and deployment state.
- Before planning or deploying the native Genie resource, regenerate its overlay from the existing
  space using `scripts/prepare_genie_bundle.py --space-id SPACE_ID --profile PROFILE --host HOST`.
  Never reuse a first-create overlay: it can overwrite user curation with an empty definition.
  Preserve the established resource identity and avoid concurrent Genie edits during export/deploy.
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
- Genie volume attachment/content-search activation remain workspace setup tasks. Source documents
  and extracted structured results are separate sources. Verify sync behavior and ordinary-user access.

## Deploying the existing dev target

The bundle's required variables have no defaults and are not stored in the repository. The values
below match the live `dev` deployment (non-secret). Omitting `viewer_projection_enabled=true` would
turn viewer projections off, so always pass the full set and review the plan.

```bash
uv run --project backend python scripts/prepare_genie_bundle.py \
  --space-id 01f1ba8f44851508b81fcc9c9a013451 --profile idp-mvp \
  --host https://dbc-97e4a372-40b1.cloud.databricks.com \
  --catalog workspace --project-schema idp_mvp --table-prefix idp_dev
make check
cd databricks_etl
VARS=(--var catalog=workspace --var project_schema=idp_mvp --var source_volume_name=idp_source
      --var artifacts_volume_name=idp_artifacts --var warehouse_id=647704f77f24020a
      --var validation_endpoint=unused --var evaluation_experiment=unused
      --var viewer_projection_enabled=true --var genie_project_name="IDP project")
databricks bundle plan   -t dev -p idp-mvp "${VARS[@]}"
databricks bundle deploy -t dev -p idp-mvp "${VARS[@]}"
databricks bundle run    -t dev -p idp-mvp "${VARS[@]}" idp_app   # activates the new app code
```

The Genie script adds the four `<prefix>_genie_*` result views to the space definition if missing. In a new
workspace run the bootstrap Job first so those views exist before the Genie resource is deployed. Attach the
source volume and enable content search manually in the space's Sources tab.

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
rm -f databricks_etl/resources/genie.generated.yml  # never deploy another workspace's Genie overlay
cd databricks_etl
VARS=(--var catalog=$CATALOG --var project_schema=$SCHEMA --var source_volume_name=idp_source
      --var artifacts_volume_name=idp_artifacts --var warehouse_id=$WAREHOUSE
      --var validation_endpoint=unused --var evaluation_experiment=unused
      --var viewer_projection_enabled=true --var genie_project_name="IDP project")
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
```

Every later deployment to that workspace is step 4's plan, deploy and run (after the Genie overlay
refresh below, if the workspace has a space). If the bootstrap is run again, deploy afterwards: replacing
views drops the grants the App binding applied.

Genie is optional and off unless an overlay is deployed. After step 2, from the repository root, create
the space with `scripts/prepare_genie_bundle.py --new-space --profile $PROFILE --host https://YOUR-WORKSPACE
--catalog $CATALOG --project-schema $SCHEMA --table-prefix $PREFIX`, then plan and deploy. Record the new
space ID from `databricks bundle summary`, delete the overlay, and before every later deployment
regenerate it with `--space-id NEW_SPACE_ID`. Attaching the source volume and enabling content search stay
manual steps in the space's Sources tab.

Historical detailed instructions and evidence: [Genie lifecycle](archive/GENIE_AUTOMATION_STATUS.md),
[Genie setup](archive/GENIE_SETUP.md), and [implementation handoff](archive/performance/intake-implementation-progress.md).
These archived notes contain superseded checkpoints; reconcile them with current code and live state.
