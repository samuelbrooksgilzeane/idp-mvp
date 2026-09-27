# Project Genie automation: verified boundaries

Decisions: one Genie space per project; no initial structured tables; attach the existing source
volume in full; users curate in Databricks; ordinary redeployments preserve their edits.
Content search should be automatic only when a supported interface is available. It must remain
off in this Free Edition workspace. No search index or preview has been enabled.

## Implemented checkpoint

The project definition now starts with an empty tables list and source-neutral instructions.
The existing management-API provisioner creates once using durable state and thereafter exports
and preserves the remote definition. A deliberate replacement still requires a reviewed remote
hash and ETag. The app describes configured sources without falsely claiming PDF search is disabled
or that particular structured views are attached. Eleven focused local tests pass.
Empty-template server acceptance has not been tested by creating a live resource.

## Automation requiring a decision or verified contract

- Native `resources.genie_spaces` requires the direct deployment engine. This project's engine
  migration has not been applied. Its installed CLI 1.14.1 schema supports `prevent_destroy`,
  but exposes no `ignore_changes` lifecycle field. A native resource alone therefore must not
  be assumed to preserve human edits. An export/reconcile step before deployment is required.
- The published Genie serialized-space example documents tables/metric views, not a volume
  attachment schema. The installed SDK and bundle volume schema expose no content-search switch.
  A verified export from a volume-enabled space or documented API contract is needed before
  implementing attachment. Do not invent `volumes` or `content_search_enabled` payload fields.
- Content-search documentation currently directs administrators to Catalog Explorer → volume →
  Overview → Content search → Enable. It triggers initial parsing/indexing. New or changed files
  require an explicit Sync now; enabling search is not continuous synchronization. Disabling it
  deletes generated content/indexes; redeployment must never disable/re-enable it automatically.
- Only managed volumes support this preview, subject to regional/preview/permission requirements.
  App embedding additionally requires the specific app origin to be allowed by an administrator.
  Use the official generated embed URL rather than treating an ordinary Genie link as proof.

## Next-workspace validation

Enable the preview and attach the existing project source volume through supported UI controls.
Export that space with the documented get-space API (include_serialized_space=true), inspect
whether its volume attachment is included, and retain a sanitized fixture for implementation tests.
If absent, attachment remains manual until an official management API is available. Enable content
search only in the explicitly selected eligible workspace with its indexing budget understood.
Do not translate manual completion into a claim that bundle automation is implemented.

Sources inspected 2026-09-27:
- https://docs.databricks.com/aws/en/dev-tools/bundles/direct
- https://docs.databricks.com/api/genie/v1/genie-space
- https://docs.databricks.com/aws/en/genie-agents/volumes
- https://docs.databricks.com/aws/en/volumes/content-search
- https://docs.databricks.com/aws/en/genie-agents/embed

## Native migration preparation (approved option 1)

`scripts/prepare_genie_bundle.py` now writes an ignored `resources/genie.generated.yml` overlay.
It opts into the direct engine, adds a protected `project_genie` resource, and wires its bundle ID
and workspace host into the existing App environment variables. The ordinary checked-in bundle
stays unchanged until this explicit preparation step. Only one provisioning owner may be used:
once native management is adopted, do not use `provision_genie.py --apply` for this same space.

First creation, from repository root:

```sh
uv run --project backend python scripts/prepare_genie_bundle.py --new-space --host https://YOUR-WORKSPACE
```

Then, from `databricks_etl`, use the same profile, target and project variables for `bundle validate`
and `bundle plan`. Review the full migration, especially existing App/Job IDs, grants, and any
replacement/deletion actions before `bundle deploy`. Do not force migration or bypass plan checks.
After deployment, record the resource ID from bundle summary and remove the generated overlay.
Never reuse a first-create overlay for a later deployment: it contains the empty initial definition.

For every later plan/deploy, export current human curation afresh:

```sh
uv run --project backend python scripts/prepare_genie_bundle.py --space-id SPACE_ID --profile PROFILE --host https://YOUR-WORKSPACE
```

Use the ID already managed by `project_genie` in the selected target's bundle state. For a space
previously managed by the old script, explicitly bind `project_genie` to that existing ID before
planning, rather than creating a duplicate. The generated overlay includes the current ETag and
exact serialized definition; a concurrent edit must cause conflict, not a blind overwrite. Confirm
this behavior in the target workspace before routine automated releases. Preparation removes stale
output before authentication, so a failed export leaves no old overlay to deploy accidentally.

The generated resource does not set broad permissions. Configure intended users/groups explicitly.
Set `genie_embed_url` to the official Share → Embed space URL, after allowing the specific app
origin. Without that URL, the app provides the native open-in-Databricks fallback. Run the App via
the bundle so resource substitutions reach its environment; the standalone app.yaml remains off.

Volume attachment and content-search activation remain manual Databricks workspace tasks under
the approved revised scope. No in-app source editor or fake automation toggle was added.

Validation checkpoint (2026-09-27): installed CLI 1.14.1 accepted the generated native overlay.
Read-only bundle plan: create project_genie, results_exporter, work_dispatcher; update idp_app,
document_extractor, document_parser, governed_data_bootstrap. Totals: 3 add, 4 change, 0 delete.
No plan was applied. Removed the temporary first-create overlay after review to prevent accidental
reuse. The existing frontend sync warning remains a CLI pattern warning; full local configuration
validation passes. Thirteen focused Genie tests pass. This is preparation evidence, not server-side
empty-space creation, iframe behavior or successful preservation under a concurrent workspace edit.
