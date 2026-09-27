# Project Genie setup

Status: code prepared; workspace migration, provisioning, permissions and embedded-user
verification are not yet applied. Genie defaults off. This is structured-data chat;
no PDF volume attachment, content-search index, prompt or model inference is created by setup.

## Deployment order

1. Apply existing table migrations first, including generic fields/records, then
   `databricks_etl/sql/create_genie_views.sql` with the same catalog/project_schema/table_prefix
   parameters as bootstrap. Bootstrap now includes this dependent task. The views retain only
   latest successful extraction per document/schema across versions. Generic projections may
   be absent for old runs; absence is not evidence of zero values. Values remain candidates.
2. Give the intended user/group USE CATALOG, USE SCHEMA and SELECT on the four curated views,
   plus warehouse CAN USE and required workspace/SQL entitlements. Use a maintainer identity
   with access to create the space. Grant ordinary users conversation access separately from
   maintainer CAN MANAGE; do not grant broad source-volume access merely to enable this page.
3. Render offline from repo root (no workspace calls):

   ```sh
   uv run --project backend python scripts/provision_genie.py --catalog workspace --schema idp_mvp --prefix idp_dev --output /tmp/idp-genie.json
   ```

   Review the output. To provision explicitly, add `--apply --profile idp-mvp
   --warehouse 647704f77f24020a --state /path/to/retained/project-genie-state.json`.
   Use a separate durable state file per workspace/project. Never delete it to retry a failure.
   An ambiguous create leaves `pending_create`: locate the created space in the workspace,
   replace that marker with its `space_id`, and preserve host/warehouse identity before retrying.
   If none exists, investigate the failed request before clearing the marker. A stale `.lock`
   after process termination should only be removed after confirming no deployer remains active.
4. Subsequent applies read the existing space and export a sibling `.remote.json` snapshot.
   A difference refuses to update. Review the snapshot, reconcile SQL-team edits into the
   versioned template, then pass the displayed `--reviewed-remote-hash`. An ETag protects the
   update from edits made after the read. Keep snapshots/state private; they may contain
   human-curated SQL or sample values. No app deployment or startup provisions a space.
5. Set bundle variables `genie_enabled`, `genie_space_id`, `genie_workspace_origin`,
   `genie_project_name` and optional `genie_embed_url`. Standalone Apps UI deployments instead
   read corresponding `IDP_GENIE_*` values in `app.yaml`; defaults there remain disabled.
   Copy the official URL from Share → Embed space. Do not manufacture an iframe URL.
   Admins must permit the app origin in allowed embedding surfaces. Without embedding,
   enable the open-in-Databricks fallback using origin and ID. HTTPS origin/ID validation and
   CSP restrict the iframe. No app service-principal credential is sent to the browser.
6. Deploy only after the existing bundle packaging validation issue is resolved. This change
   intentionally uses the management API rather than changing the existing deployment engine.
   Native bundle Genie resources require the direct engine; changing that is a separate review.

## Small workspace verification

Start with metadata-only checks. Do not upload batches, run parsers, start recurring recovery,
create content-search indexes or run benchmarks on Free Edition. Metadata checks do not start
SQL compute but still require valid credentials and available API quota. A small test cannot
be guaranteed within quota when the remaining account allowance is unknown.

Then, if quota is available, manually ask **one** document-count question against existing
views and compare to its supplied SQL. No new PDFs or extraction is needed. This uses SQL/Genie
resources, so stop on quota errors rather than retrying repeatedly. Test ordinary-user access,
missing grants, signed-out state, blocked third-party cookies, copy results, fallback link and
refresh of `/ask-genie` in the actual App. Browser frame errors are not reliably observable;
the fallback link is always present. App selections do not scope Genie. Unsent text can be lost
when leaving the route. Record results rather than treating local mocks as workspace proof.

## Free Edition and volumes

The official Free Edition limitations do not establish a blanket ban on Genie volume attachment.
File analysis is a Beta requiring the **Analyze Files in Volumes with Genie Agents** preview,
supported region/Agent mode and volume permissions. This workspace's availability remains
unverified. An unavailable preview can be evaluated in another eligible workspace; use separate
state/config and apply its own grants. Structured views above do not need volume attachment.
Attaching a volume exposes the entire volume, not selected folders; content search adds indexing.
Keep that optional capability disabled pending a separate scope/cost decision.

Official references (reviewed 2026-09-27):
- [Free Edition limitations and fair usage](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations)
- [Genie file analysis prerequisites](https://docs.databricks.com/aws/en/genie-agents/volumes)
- [Embedding and user authentication](https://docs.databricks.com/aws/en/genie-agents/embed)
- [Genie management API](https://docs.databricks.com/api/genie/v1/genie-space)
- [Direct deployment engine](https://docs.databricks.com/aws/en/dev-tools/bundles/direct)
