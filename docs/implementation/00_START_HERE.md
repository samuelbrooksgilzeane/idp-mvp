# IDP MVP Implementation Pack

## Purpose

Implement the MVP as a sequence of small, deployable Git commits. At the end of every commit:

- The repository must build and its existing tests must pass.
- The new capability must be independently demonstrable.
- No later-stage code should be partially introduced.
- The commit must be safely revertible without deleting persisted data.

The first reference profile is `invoice_v1`, using the existing labelled invoice PDFs and truth data. The framework must remain generic enough to add `contract_v1` later.

## Required order

| Order | Specification | Capability after completion | Stakeholder progress statement |
|---:|---|---|---|
| 1 | [Project foundation](02_COMMIT_PROJECT_FOUNDATION.md) | App and Asset Bundle build and validate | “The deployable application foundation is in place.” |
| 2 | [Data foundation](03_COMMIT_DATA_FOUNDATION.md) | Prefixed volumes, tables and views exist | “The governed storage and audit model is ready.” |
| 3 | [Upload and registry](04_COMMIT_UPLOAD_AND_REGISTRY.md) | PDFs can be uploaded, identified and tracked | “We can ingest and track documents without processing them yet.” |
| 4 | [Parsing pipeline](05_COMMIT_PARSING_PIPELINE.md) | PDFs can be parsed into retained layout-aware data | “We can convert PDFs into structured page and element data.” |
| 5 | [Parsed-document viewer](06_COMMIT_PARSED_DOCUMENT_VIEWER.md) | Users can visually inspect parsed pages and elements | “Users can now verify what the parser actually read.” |
| 6 | [Schema registry and viewer](07_COMMIT_SCHEMA_REGISTRY_AND_VIEWER.md) | Users can see the exact versioned extraction specification | “The extraction requirements are transparent and controlled.” |
| 7 | [Extraction pipeline](08_COMMIT_EXTRACTION_PIPELINE.md) | Invoice fields, confidence and citations are produced | “We can extract typed invoice data with evidence.” |
| 8 | [Extraction evidence UI](09_COMMIT_EXTRACTION_EVIDENCE_UI.md) | Selecting a field highlights its source evidence | “Users can trace each extracted value back to the PDF.” |
| 9 | [Deterministic validation](10_COMMIT_DETERMINISTIC_VALIDATION.md) | Technical and arithmetic checks identify exceptions | “We can automatically detect missing, malformed and inconsistent values.” |
| 10 | [LLM validation](11_COMMIT_LLM_VALIDATION.md) | Configured exceptions receive source-grounded adjudication | “Ambiguous values can be checked against their evidence without being auto-corrected.” |
| 11 | [Evaluation and demo](12_COMMIT_EVALUATION_AND_DEMO.md) | Accuracy is measured and the stakeholder demo is repeatable | “The MVP is measured, explainable and ready to demonstrate.” |

Read [Technical contracts](01_TECHNICAL_CONTRACTS.md) before implementing Commit 1. Those contracts apply to every commit.

Before beginning implementation, follow [First Codex session](FIRST_CODEX_SESSION.md). It contains the complete repository setup, remote-linking, verification and Commit 1 prompt.

## Milestone boundaries

### Milestone A — Parsing MVP

Commits 1–5. Tag the verified commit:

```text
mvp-parsing
```

At this point the user can upload, parse and inspect documents, but no domain fields are extracted.

### Milestone B — Extraction MVP

Commits 6–8. Tag the verified commit:

```text
mvp-extraction
```

At this point the user can see the extraction schema, run extraction and trace values to source evidence, but no validation decision is made.

### Milestone C — Validated MVP

Commits 9–11. Tag the verified commit:

```text
mvp-validation
```

At this point the user can run transparent validation and view measured benchmark performance. Human correction remains a future milestone.

## Commit discipline

For every implementation commit:

1. Implement only the named scope.
2. Add or update the tests required by that specification.
3. Run all relevant existing checks, not only the newly added tests.
4. Update [Progress tracker](PROGRESS_TRACKER.md).
5. Commit code, tests and documentation together.
6. Do not squash the numbered capability commits into one large commit.
7. Use follow-up fix commits only when a reviewed commit has already been shared; otherwise fix before committing.

Do not combine refactoring, dependency upgrades or styling changes with a capability commit unless they are strictly necessary for that capability.

## Rollback rules

- Git reverts must disable the reverted capability without dropping its tables or deleting uploaded files.
- Database bootstrap changes are forward-only and idempotent.
- Never put destructive `DROP`, recursive deletion or input-volume cleanup in an application startup path.
- A rollback may leave unused tables or columns; remove them only in a separately reviewed migration.
- Raw parse, extraction and validation runs are immutable audit records.
- Feature-specific API routes and UI controls should disappear cleanly when their commit is reverted.

## Pull-request strategy

Recommended review structure:

- PR 1: Commits 1–5 — parsing milestone.
- PR 2: Commits 6–8 — extraction milestone.
- PR 3: Commits 9–11 — validation milestone.

Each PR must remain reviewable commit-by-commit. Merge only after the milestone tag acceptance criteria pass in the development environment.

## Deliberate non-goals

Do not implement during these commits:

- Human correction or approval.
- Canonical/approved outputs.
- Contract calculations.
- Invoice-to-GL matching.
- Risk-case decisions.
- Genie, Knowledge Assistant or Supervisor Agent integration.
- External VDR or email ingestion.

The MVP ends at `VALIDATED_PASS` or `REVIEW_REQUIRED`; neither state constitutes human approval.
