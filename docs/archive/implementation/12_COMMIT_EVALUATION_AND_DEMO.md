# Commit 11 — Evaluation and Stakeholder Demo

## Outcome

The MVP has reproducible benchmark evidence and a repeatable demonstration of parsing, extraction and validation.

## Recommended commit

```text
feat(idp): add extraction evaluation and mvp demo
```

## Scope

Implement:

- A reproducible benchmark job using the existing labelled invoice dataset.
- Prediction-to-truth joins by stable `document_id`.
- Spark-based metrics and MLflow logging.
- Schema-promotion guardrails.
- End-to-end smoke tests and a stakeholder demo runbook.

Do not add human correction, approval or invoice-to-ledger matching.

## Required metrics

- Extraction error rate.
- Required-field coverage.
- Per-field normalised exact match for strings, dates and currency.
- Per-field numeric match within configured tolerance.
- Whole-document exact-match rate.
- Citation coverage.
- Confidence distributions for correct and incorrect predictions.
- Deterministic-validation false-pass and false-fail rates.
- LLM-validation pass/fail/uncertain and technical-failure counts.

Log schema ID/version/hash, parser and extractor versions, prompt/rule versions, code commit and truth-dataset version with every run.

## Implementation requirements

1. Keep truth data separate from application production outputs.
2. Document every normalisation and numeric tolerance used for scoring.
3. Use Spark aggregations for the full benchmark; do not collect the full dataset to the driver.
4. Allow explicit small sampling only for qualitative LLM diagnostics.
5. Produce per-field results so aggregate performance cannot conceal a high-risk-field regression.
6. Block schema promotion when a configured high-risk metric regresses beyond its tolerance.
7. Make evaluation rerunnable from a Databricks Job and locally testable with small fixtures.
8. Record benchmark failures as evidence, not by rewriting previous MLflow runs.

## End-to-end tests

- Upload, parse, inspect, choose schema, extract, inspect evidence and validate.
- Duplicate upload behavior.
- Parse, extraction and model-endpoint failure recovery.
- Historical run provenance and latest-successful selection.
- Authentication and cross-document access controls.
- No browser-supplied infrastructure identifiers.
- Bundle validation, backend tests and frontend build.

## Stakeholder demo runbook

Use fixed, approved sample documents and rehearse this sequence:

1. Upload an invoice and show its governed identity.
2. Trigger parsing and inspect page elements.
3. Display `invoice_v1` before extraction.
4. Run extraction and trace `total_amount` to its citation.
5. Show a balanced invoice passing deterministic reconciliation.
6. Show an ambiguous or failing example routed to review.
7. Show the latest benchmark metrics and version provenance.

The demo must label confidence thresholds as initial policies and must not describe `VALIDATED_PASS` as human approval.

## Tests

- Metric calculations against hand-computed fixtures.
- Normalisation and numeric-tolerance cases.
- Dataset and prediction join completeness.
- High-risk regression guardrail.
- MLflow parameter/metric provenance.
- End-to-end happy path and selected failure paths.
- Demo dataset availability and runbook commands.

## Demonstration

Run the complete stakeholder path from upload to validation, then open the benchmark result and show which exact code, schema and dataset versions produced it.

## Rollback boundary

Reverting removes evaluation and demo assets only. All operational MVP stages remain available; historical MLflow runs are retained.

## Progress statement

> The MVP is now measurable, explainable and repeatable from PDF upload through validation.

## Definition of done

- The benchmark runs from a clean deployed environment.
- Metrics and provenance are logged reproducibly.
- The full demo succeeds with fixed samples.
- All milestone acceptance tests pass.
- Tag the verified commit `mvp-validation`.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

