# Commit 9 — Deterministic Validation

## Outcome

The system identifies missing, malformed and arithmetically inconsistent invoice values using transparent rules.

## Recommended commit

```text
feat(idp): add deterministic invoice validation
```

## Scope

Implement:

- A typed, non-mutating validation interface.
- Generic technical validators.
- Deterministic `invoice_v1` rules.
- `POST /api/documents/{document_id}/validate` for deterministic validation only.
- Validation-run, latest-results and summary APIs.
- Validation summary and issue UI linked to extraction evidence.

Do not call an LLM in this commit.

## Validation result contract

Every rule returns a stored result containing:

- `rule_id`, optional `field_path`, validator type and validator version.
- Severity: `INFO`, `WARNING` or `BLOCKING`.
- Status: `PASS`, `FAIL`, `UNCERTAIN` or `SKIPPED`.
- Human-readable message, actual and expected values, and evidence metadata.

Rules return observations; they must never edit extracted values or populate an approved record.

## Required validators

Technical rules:

- Successful parse and extraction provenance.
- Required-field presence.
- Expected type/cast success.
- Valid date and currency formats.
- Citation presence where required.
- Confidence threshold policy.
- Duplicate-document signal.

Invoice rules:

- Reconcile `subtotal - discount_amount + tax_amount` to `total_amount` using `Decimal` and the schema-configured tolerance.
- Reject a negative total unless the schema explicitly allows credit notes.
- Require a non-empty normalised invoice number.
- Check currency is a three-letter uppercase code and, when configured, is in the allowed list.

## Null and uncertainty rules

1. Never silently convert a missing optional discount to zero unless the schema policy explicitly defines that meaning.
2. If required inputs for a calculation are absent, return `UNCERTAIN` or `SKIPPED`, never `PASS`.
3. Low confidence alone is not proof of error; normally record `WARNING` or `UNCERTAIN` unless a high-risk policy makes it blocking.
4. Use exact decimal arithmetic; do not use an LLM for arithmetic, casting or exact-match checks.

## Orchestration and status

The request selects a successful extraction run or defaults deterministically to the latest one. Persist all results and their rule versions. Set:

- `VALIDATED_PASS` when no blocking failure or unresolved blocking uncertainty exists.
- `REVIEW_REQUIRED` otherwise.

Neither state means a person approved the document.

## Tests

- Typed result-model validation.
- Required, type, date, currency, citation and confidence rules.
- Decimal reconciliation at, within and outside tolerance.
- Missing subtotal, discount, tax and total permutations.
- Negative total and credit-note policy.
- Validator exceptions become an auditable non-pass state.
- Results never mutate extraction rows.
- Summary counts and status transition.
- UI filtering and evidence links.
- No call to a model endpoint.

## Demonstration

Show one balanced invoice becoming `VALIDATED_PASS`, then change a fixture so its total is outside tolerance and show the blocking rule and `REVIEW_REQUIRED` result linked to the source evidence.

## Rollback boundary

Reverting removes deterministic validation code, routes and UI. Extraction remains fully usable; stored validation runs remain immutable audit records.

## Progress statement

> We can now automatically detect missing, malformed and inconsistent values with explainable rules.

## Definition of done

- Every required rule has positive, failure and null-path tests.
- All outcomes are traceable to an extraction run and rule version.
- Validation cannot correct or approve a value.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

