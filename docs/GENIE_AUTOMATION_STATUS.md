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
