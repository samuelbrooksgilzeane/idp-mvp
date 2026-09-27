# Release evidence — pending workspace validation

This is an evidence ledger, not a throughput claim. No 1,000-file load test has run.
Free Edition is not being used for bulk uploads, repeated inference or indexing.

| Check | Evidence on 2026-09-27 | Remaining gate |
| --- | --- | --- |
| Stages 1–5 local regression | Prior checkpoint: 109 backend / 73 frontend tests passed | Workspace migrations, permissions and small workflow test |
| Stage 6 Genie local checks | 23 backend / 77 frontend tests; type/lint/build checks passed | SQL view acceptance, API provisioning, user grants and iframe |
| Metadata smoke | Attempt blocked by CLI authentication; browser redirects to sign-in | Refresh login and run the bounded script once |
| Bundle packaging | Full local configuration validator fails missing sync.include contract | Resolve and inspect actual synced production assets before deployment |
| Synthetic export scale | Existing writer correctness tests; no performance measurement recorded | Local retained-fixture benchmark with memory/disk tracking |
| Navigation p50/p95 | No browser performance measurement recorded | Warm versus cold API, network, image decode and render timing |
| Real PDFs / AI throughput | Not run | User-managed eligible workspace and explicit test budget |
| Mixed usage/recovery | Local correctness checks only | Small supervised recovery test before any scale test |

## Next steps in order

1. Refresh `idp-mvp` OAuth. Run metadata-only smoke once from the repository root:

   ```sh
   uv run --project backend python scripts/workspace_smoke.py --profile idp-mvp --host https://dbc-97e4a372-40b1.cloud.databricks.com --warehouse 647704f77f24020a
   ```

   Three explicit GET calls maximum, pages capped at five, stop on first error. SDK authentication,
   host discovery and its short retry window can add network requests. No SQL, job submissions,
   Genie prompts, uploads, scheduling or mutations. Output contains counts/status, not document
   contents, names or credentials. Do not loop on errors. This does not certify remaining quota.
2. Resolve packaging validation, review deployment diff and migration order. Keep automatic
   preparation, bulk extraction/export, viewer projections and Genie off until their prerequisites
   are verified. Recovery schedule remains PAUSED.
3. Apply reviewed migrations/grants in the chosen workspace, then activate one feature at a time.
   Verify with one existing small document/result where possible. A single read-only Genie count
   question is a separate, quota-consuming check, not part of the metadata script.
4. Record ordinary-user Genie embedding and fallback checks using `docs/GENIE_SETUP.md`.
5. Implement/run local synthetic performance fixtures; real scale testing remains user-managed.
   Record dataset hashes, result/child-row counts, pages, bytes, versions, runtime/compute,
   concurrency, cold starts separately, p50/p95, peak RSS, temp disk, retries and cost where known.
   Synthetic tests do not establish AI throughput or full workspace recovery.

Stop on quota/429/service errors. Do not increase concurrency or automatically retry large batches.
A different workspace needs its own profile, deployment state, resources and grants; never reuse
this workspace's Genie state file or assume features/quotas are equivalent.
