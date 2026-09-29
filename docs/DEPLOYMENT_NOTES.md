# Deployment notes

Updated: 29 September 2026. Release gates live in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).

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
- Keep recovery schedules paused until smoke verification. Start/stop of the app is independent of Jobs,
  warehouses and schedules; it is not a shutdown mechanism for all project compute.
- The source volume contains uploaded PDFs. App deletion removes the PDF then marks its registry row
  deleted; it does not purge retained results or generated artifacts. Direct SQL deletion is different.
- Genie volume attachment/content-search activation remain workspace setup tasks. Source documents
  and extracted structured results are separate sources. Verify sync behavior and ordinary-user access.

Historical detailed instructions and evidence: [Genie lifecycle](archive/GENIE_AUTOMATION_STATUS.md),
[Genie setup](archive/GENIE_SETUP.md), and [implementation handoff](archive/performance/intake-implementation-progress.md).
These archived notes contain superseded checkpoints; reconcile them with current code and live state.
