# Deployment guide

How to deploy the IDP app: to the existing dev workspace from a development laptop, or to any
other Databricks workspace with one settings file and one command, including the document chat.
Background and constraints are in [DEPLOYMENT_NOTES.md](DEPLOYMENT_NOTES.md).

## 1. Set up a development machine (once per laptop)

Only needed to develop and to deploy the dev workspace with `make deploy` (section 2). Deploying to
another workspace (section 3) needs only Python 3.10 or later and the Databricks CLI.

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
  bootstrap) or changes chat settings, run `make workspace-setup` after `make deploy`. It runs the
  `workspace_setup` job (migrations, chat provisioning, the app's grants), then deploys again.
  The dev target keeps its gate G7 Supervisor as the chat endpoint override; running workspace
  setup there also creates a Knowledge Assistant, which uses Free Edition quota.
- `make grants` re-applies the app's access grants on their own. It only adds access, so repeating it
  is safe.

## 3. Deploy to another workspace

Prerequisites in the workspace: an existing Unity Catalog catalog you may create a schema in, a SQL
warehouse, serverless Jobs, and AI Functions (`ai_parse_document`, `ai_extract`) in the workspace's
region. For document chat, Agent Bricks (Knowledge Assistant) must be on: a workspace admin turns it
on under **Settings > Previews** (search for "Agent Bricks"). Without it, set `chat_mode: "off"`.

On your machine: Python 3.10 or later and the Databricks CLI (`brew tap databricks/tap && brew
install databricks` on macOS; [other systems](https://docs.databricks.com/dev-tools/cli/install.html)),
and a copy of the repository (`git clone`, or a ZIP of the branch). No uv, Node or make.

### 3.1 Create the workspace's settings file

```bash
cp databricks_etl/targets/example.yml.template databricks_etl/targets/us.yml
```

`us` is the target name: lowercase letters, digits and hyphens, up to 20 characters, not `dev` or
`prod`. In the copy, replace `example:` with the same name and fill in the values marked `YOUR_`:
the workspace URL, the catalog and the SQL warehouse id (**SQL Warehouses** > your warehouse, or
`databricks warehouses list` once signed in), and set `deployment_name`, usually
`idp-mvp-<target>`. Every other value has a working default and a comment. Nothing in the file is
secret: commit it, so the next deploy (or a colleague) uses the same values.

`deployment_name` names the app, and so its address, plus the jobs and the Knowledge Assistant.
Changing it later creates a new app at a new address.

### 3.2 Sign in

```bash
databricks auth login --host https://YOUR-WORKSPACE.cloud.databricks.com
```

Where a browser sign-in is unavailable (a cluster's web terminal), use a personal access token:
`databricks configure --host https://YOUR-WORKSPACE.cloud.databricks.com` and paste the token.

### 3.3 Deploy with one command

```bash
python3 scripts/deploy_workspace.py us             # Windows: py scripts\deploy_workspace.py us
make deploy-workspace TARGET=us                    # the same, where make is installed
```

Add `--profile NAME` (or `PROFILE=NAME` with make) when several CLI profiles point at the same
workspace. The script says what each step does:

1. checks the CLI, the settings file and `frontend/dist`, and validates the settings with the CLI;
2. deploys the jobs and the app. **On the first deployment the app step fails, as expected**: the
   app's grants name tables that the next step creates. The script carries on;
3. runs the `workspace_setup` job: schema, volumes, tables and migrations; the Knowledge Assistant
   (and in `supervisor` mode the Supervisor), one index sync, and the app's grants. Its run link is
   printed; it takes several minutes;
4. deploys again, grants the new app its remaining access (first deployment only), starts the app
   and prints its address.

Everything is safe to repeat. After fixing a failure, run the same command again. Later
deployments, and changes to the settings file, use the same command.

The first Knowledge Assistant index can take a long time (over 40 minutes on the US workspace,
shown as "finalizing processing"); until it finishes, chat answers that it has no documents.

### 3.4 Optional: let users import folders

Who may drop folders in for import is an access decision, so this grant is not automated. Users need
to see and write into the import volume. In a SQL editor, with your catalog:

```sql
GRANT USE CATALOG ON CATALOG YOUR_CATALOG TO `account users`;
GRANT USE SCHEMA ON SCHEMA YOUR_CATALOG.idp_mvp TO `account users`;
GRANT READ VOLUME, WRITE VOLUME ON VOLUME YOUR_CATALOG.idp_mvp.idp_import TO `account users`;
```

Replace `account users` with a narrower group if only some people should import. The import never
deletes files from the folder; remove a folder once the app shows its files registered.

### 3.5 Keeping a hand-deployed app and its address (the US workspace)

The US workspace was deployed by hand on 10–11 October as app `idp-mvp-dev`, with jobs named
`[dev <user>] ...`. Its settings file, `databricks_etl/targets/us.yml`, keeps that app, its address,
its tables and its Knowledge Assistant: `deployment_name: idp-mvp-dev`, `catalog: idp_testing`,
`table_prefix: idp_dev`. Fill in its workspace URL and warehouse id, then deploy **once** with
`--adopt-app`, which makes this deployment take over the existing app instead of creating one:

```bash
python3 scripts/deploy_workspace.py us --adopt-app     # first time only
python3 scripts/deploy_workspace.py us                 # every later deploy
```

The jobs are created afresh under the new names (`idp-mvp-dev-...`, no `[dev <user>]` prefix). The
existing Knowledge Assistant `idp-mvp-dev-documents-ka` is found by name and reused, so its index
is kept. Once the app works, delete the old `[dev <user>] idp-mvp-dev-...` jobs in **Jobs & Pipelines**
(the old work dispatcher would otherwise keep running hourly), and the unused
`idp-mvp-dev-chat-supervisor` if it exists. From then on, deploy the US workspace only with the
script: the old `databricks bundle ... -t dev` commands now point at the Free Edition workspace,
and `bundle destroy -t dev` against US would delete the app.

## 4. Deploy from a workspace Git folder (no laptop)

1. **Workspace > Create > Git folder**, with the repository URL and the branch to deploy.
2. Add the settings file `databricks_etl/targets/<name>.yml` (section 3.1): commit it to the
   repository first, or create it in the Git folder (**Create > File**).
3. Open a terminal in the workspace: a cluster's **Web terminal** (Compute > your cluster >
   Apps). Sign in with a token (section 3.2), then:

   ```bash
   cd /Workspace/Users/<you>/<git folder>
   python3 scripts/deploy_workspace.py <name>
   ```

   The script removes `DATABRICKS_CLUSTER_ID`, which the web terminal sets, so jobs keep running on
   serverless compute. A Git folder has no `.git`, so `.gitignore` does not apply there; the bundle's
   sync excludes keep `.venv`, `node_modules` and caches out of the deployment instead.
4. **The bundle UI's Deploy button** (opening `databricks_etl/databricks.yml` in the Git folder)
   only deploys; it does not run workspace setup or start the app. On a first deployment, run the
   `workspace_setup` job and deploy again by hand. *To be recorded by live check L8: whether the
   button offers the settings-file target and whether the app then starts.*

## 5. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| The first deploy of a target reports an app error | Expected: the app's grants name tables that workspace setup creates. The script continues; later deploys succeed. |
| `Stopped: The settings in targets/<name>.yml, or the sign-in, have a problem` | The CLI's message above it names the setting. If it says you are not signed in, run `databricks auth login --host ...` (section 3.2). |
| Workspace setup fails in `provision_chat` naming Agent Bricks | Agent Bricks is off. A workspace admin turns it on (Settings > Previews), or set `chat_mode: "off"`; run the command again. |
| Chat answers "Document chat is still being set up" | Chat provisioning has not recorded its endpoint yet (setup failed or has not run). Run the command again; the app picks the endpoint up within five minutes. |
| Chat answers that it has no documents | The first index is still running, or the sync did not start. Wait, or press **Sync** on the Knowledge Assistant (Agents in the sidebar). |
| A page fails with `INSUFFICIENT_PRIVILEGES` on a table | The app's grants did not run after the table appeared. Run `databricks bundle run -t <name> app_access_grants` from `databricks_etl/`, or the deploy command again. A test now fails when a table is neither bound to the app nor granted. |
| An app deploy fails with `exporting symlink ... in source format is not supported` | Generated folders (`backend/.venv`) were uploaded from a copy without `.git`. Update to a version whose `databricks.yml` has the `sync.exclude` list. |
| Jobs fail saying the cluster is not all-purpose | `DATABRICKS_CLUSTER_ID` was set (cluster web terminal) while running `databricks bundle` by hand against the `dev` target. Run `unset DATABRICKS_CLUSTER_ID` first; the deploy script does this itself. |
| `uv` fails writing its cache under `/Workspace` ("operation not permitted") | Deploying does not need uv. Use the deploy script; `make check` belongs on a development machine. |
| The first deploy fails with "already exists" for the app | An app with that name exists outside this deployment. Run the script again with `--adopt-app` to keep it and its address (section 3.5). |
| A second person's deploy tries to create everything again | Deployment state is kept per deploying user. Deploy each workspace as the same user. |
| Folder import fails with `Already running asyncio in this thread` | Fixed on 11 October. Deploy again, then press **Retry** on the failed batch. |
| Pages fail after re-running the bootstrap job on its own | Replacing views drops grants. Run `workspace_setup` (or `make workspace-setup`) rather than the bootstrap alone, then deploy. |
| Free Edition: a second Supervisor cannot be created | Free Edition allows one Supervisor Agent; delete the unused one first. Its Knowledge Assistant indexes documents but cannot answer there (gate G7). |

If a step fails in a way not listed here, keep its full output: the job run link printed by the
script links to each task's log in the workspace.
