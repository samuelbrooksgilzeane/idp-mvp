# Deployment guide

Step-by-step commands to deploy the IDP app from any machine: a fresh laptop, the existing dev
workspace, or a brand-new Databricks workspace, including the document chat (Knowledge Assistant
and Supervisor). Background and constraints are in [DEPLOYMENT_NOTES.md](DEPLOYMENT_NOTES.md).

Run each section in one terminal session, so shell variables carry from one command to the next.

## 1. Set up a new machine (once per laptop)

macOS with Homebrew:

```bash
brew install uv node
brew tap databricks/tap && brew install databricks
```

Get the code. Use the branch you deploy (`feat/dark-blue-ui` until it is merged to `main`):

```bash
git clone https://github.com/samuelbrooksgilzeane/idp-mvp.git
cd idp-mvp
git checkout feat/dark-blue-ui
make setup            # pinned Python and frontend dependencies
```

On a machine that already has the repository, update it instead:
`git fetch origin && git checkout feat/dark-blue-ui && git pull`.

## 2. Deploy to the existing dev workspace

The `dev` target in `databricks_etl/databricks.yml` already holds this workspace's values (catalog,
warehouse, viewer projections, chat endpoint), so no variables are needed.

```bash
databricks auth login --host https://dbc-97e4a372-40b1.cloud.databricks.com --profile idp-mvp
make deploy        # tests, build, deploy, restart the app
```

- When a release adds a database migration (a new file under `databricks_etl/sql/` wired into the
  bootstrap), run `make bootstrap` after `make deploy`. It runs the migrations, re-applies the
  app's grants, then deploys again.
- `make grants` re-applies the app's access grants on their own. It only adds access, so repeating it
  is safe.

## 3. Deploy to a new workspace

Prerequisites in the new workspace: an existing Unity Catalog catalog you may create a schema in, a
SQL warehouse, serverless Jobs, and AI Functions (`ai_parse_document`, `ai_extract`) available in the
workspace's region.

> **Always pass the workspace's variables.** The `dev` target holds the *original* dev workspace's
> warehouse and chat endpoint. Every `make` command for another workspace must pass
> `BUNDLE_VARS`, or it deploys pointing at the original workspace's resources. The steps below keep
> the values in a file in your home directory, so each new terminal (or laptop) just sources it.

### 3.1 Sign in and save the workspace's values

```bash
databricks auth login --host https://YOUR-WORKSPACE.cloud.databricks.com --profile new-ws
databricks warehouses list -p new-ws        # copy your SQL warehouse's id
```

```bash
cat > ~/.idp-new-ws.env <<'EOF'
export PROFILE=new-ws
export HOST=https://YOUR-WORKSPACE.cloud.databricks.com
export CATALOG=YOUR_CATALOG
export WAREHOUSE=YOUR_WAREHOUSE_ID
export BASE="--var catalog=$CATALOG --var project_schema=idp_mvp --var source_volume_name=idp_source --var artifacts_volume_name=idp_artifacts --var warehouse_id=$WAREHOUSE --var viewer_projection_enabled=true"
EOF
source ~/.idp-new-ws.env
```

These values are not secrets. On another laptop, create the same file again (or copy it), then
`source` it.

### 3.2 First deployment, with chat off

```bash
make deploy-first PROFILE=$PROFILE BUNDLE_VARS="$BASE --var chat_endpoint=' '"
```

This runs the checks, deploys, runs the bootstrap (schema, volumes, tables, views, migrations and the
app's grants), deploys again and starts the app. **The first deploy step reports an app error; that
is expected.** The app's bound grants name tables that only the bootstrap creates, and the command
carries on.

`chat_endpoint=' '` turns chat off until the agents exist. Without it, the dev target's endpoint is
used, which does not exist in this workspace, and the grants step fails.

The app's address:

```bash
databricks apps get idp-mvp-dev -p $PROFILE -o json | python3 -c 'import json,sys; print(json.load(sys.stdin)["url"])'
```

### 3.3 Turn on document chat

Create the Knowledge Assistant and the Supervisor (by name; safe to re-run, never deletes):

```bash
cd scripts
uv run --project ../backend python provision_chat.py --host $HOST --profile $PROFILE \
  --target dev --catalog $CATALOG --project-schema idp_mvp --dry-run      # preview only
uv run --project ../backend python provision_chat.py --host $HOST --profile $PROFILE \
  --target dev --catalog $CATALOG --project-schema idp_mvp
cd ..
```

Look up the endpoint and Knowledge Assistant id, and save the chat settings with the others:

```bash
SUPERVISOR_ENDPOINT=$(databricks api get /api/2.1/supervisor-agents -p $PROFILE | python3 -c 'import json,sys; print(next(a["endpoint_name"] for a in json.load(sys.stdin)["supervisor_agents"] if a["display_name"]=="idp-mvp-dev-chat-supervisor"))')
KA_ID=$(databricks api get /api/2.1/knowledge-assistants -p $PROFILE | python3 -c 'import json,sys; print(next(a["id"] for a in json.load(sys.stdin)["knowledge_assistants"] if a["display_name"]=="idp-mvp-dev-documents-ka"))')
echo "export CHAT=\"\$BASE --var chat_endpoint=$SUPERVISOR_ENDPOINT --var ka_sync_enabled=true --var ka_id=$KA_ID\"" >> ~/.idp-new-ws.env
source ~/.idp-new-ws.env
```

Deploy with chat on, then grant the app access to the agents:

```bash
make deploy PROFILE=$PROFILE BUNDLE_VARS="$CHAT"
make grants PROFILE=$PROFILE BUNDLE_VARS="$CHAT"
```

The **Ask documents** page appears in the sidebar. The agents' serving endpoints can take a few
minutes to become ready after they are created. `ka_sync_enabled=true` asks the Knowledge
Assistant to re-index after documents are added or deleted.

### 3.4 Optional: let users import folders

Who may drop folders in for import is an access decision, so this grant is not automated. Users need
to see and write into the import volume:

```bash
for statement in "GRANT USE CATALOG ON CATALOG $CATALOG TO \`account users\`" \
  "GRANT USE SCHEMA ON SCHEMA $CATALOG.idp_mvp TO \`account users\`" \
  "GRANT READ VOLUME, WRITE VOLUME ON VOLUME $CATALOG.idp_mvp.idp_import TO \`account users\`"; do
  databricks api post /api/2.0/sql/statements -p $PROFILE --json "$(python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2], "wait_timeout": "50s"}))' "$WAREHOUSE" "$statement")"
done
```

Replace `account users` with a narrower group if only some people should import. The import never
deletes files from the folder; remove a folder once the app shows its files registered.

### 3.5 Later deployments to that workspace

```bash
source ~/.idp-new-ws.env
make deploy    PROFILE=$PROFILE BUNDLE_VARS="$CHAT"   # normal deploys
make bootstrap PROFILE=$PROFILE BUNDLE_VARS="$CHAT"   # only when a release adds migrations
```

Before chat is set up, use `BUNDLE_VARS="$BASE --var chat_endpoint=' '"` instead of `"$CHAT"`.

## 4. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| The first deploy in a new workspace reports an app error | Expected: the app's grants name tables the bootstrap creates. `make deploy-first` continues; later deploys succeed. |
| The grants job fails with `Expected one Supervisor Agent serving ...` | `chat_endpoint` names an endpoint this workspace does not have. Pass `--var chat_endpoint=' '` (chat off) or the endpoint from step 3.3. |
| A deploy to a new workspace fails naming a warehouse or endpoint it does not have | `BUNDLE_VARS` was not passed, so the original dev workspace's values were used. Run `source ~/.idp-new-ws.env` and deploy again with it. |
| Chat answers fail with `PERMISSION_DENIED` | Run `make grants` with the same `BUNDLE_VARS`. It grants the app access to the Supervisor, Knowledge Assistant, both endpoints and the trace experiment. |
| Pages fail after re-running the bootstrap on its own | Replacing views drops grants. Run `make bootstrap` (bootstrap, then deploy) rather than the job alone, or follow with `make deploy`. |
| `provision_chat.py` cannot create a second Supervisor | Free Edition allows one Supervisor Agent; delete the unused one first. Its Knowledge Assistant indexes documents but cannot answer there. |

If a step fails in a way not listed here, keep its full output: the job run URL that `make` prints
links to each task's log in the workspace.
