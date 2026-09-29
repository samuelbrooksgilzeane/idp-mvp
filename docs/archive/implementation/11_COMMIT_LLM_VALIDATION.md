# Commit 10 — Source-Grounded LLM Validation

## Outcome

Configured ambiguous or high-risk values can receive a cited, structured second check without automatic correction.

## Recommended commit

```text
feat(idp): add source-grounded llm validation
```

## Scope

Implement:

- A source-controlled, versioned validation prompt.
- A configurable Databricks-hosted serving-endpoint client.
- Eligibility rules for LLM validation.
- Structured-response validation and failure handling.
- `LLM` results in the existing validation orchestration and UI.

Do not add editing, approval or external model-service integration.

## Eligibility

Call the LLM only when at least one is true:

- The field policy sets `llm_validate: true`.
- A deterministic result is `FAIL` or `UNCERTAIN` and its rule permits LLM adjudication.
- A configured high-risk field requires source verification.

The LLM cannot override deterministic arithmetic. Its result is an additional observation used by the stored summary policy.

## Grounded request

Send the minimum required context:

- Field name, description, type and extracted value.
- The deterministic issue, when present.
- Cited text and only the necessary nearby parsed elements.
- Page number and citation metadata.
- An instruction to decide solely from the supplied evidence.

Do not send a whole document when cited evidence is sufficient. Do not log source text in normal application logs.

## Required response

Validate the response with Pydantic or JSON Schema:

```json
{
  "status": "PASS | FAIL | UNCERTAIN",
  "reason": "short source-grounded explanation",
  "suggested_value": null,
  "evidence": "short supporting source fragment"
}
```

## Implementation requirements

1. Use only a configured Databricks-hosted endpoint; endpoint identity comes from trusted configuration.
2. Store endpoint/model identity when available, prompt version, prompt hash, latency and error status.
3. Validate structured output and retry malformed output once.
4. Endpoint failure, timeout or repeated malformed output becomes `UNCERTAIN`, never `PASS`.
5. A plausible value without supporting supplied evidence cannot pass.
6. Store `suggested_value` only as a review hint; never write it into extraction or candidate tables.
7. Clearly distinguish `LLM` and `DETERMINISTIC` results in API responses and UI.
8. Recompute the document summary from persisted results using a versioned policy.

## Tests

- Eligibility matrix prevents unnecessary calls.
- Minimal grounded request construction.
- Valid pass, fail and uncertain structured responses.
- One retry for malformed output.
- Timeout, endpoint error and second malformed response become uncertain.
- Prompt hash/version and endpoint provenance persistence.
- Suggested values cannot mutate extracted values.
- Arithmetic failures are not overridden.
- UI labels, evidence display and filters distinguish validator types.
- Redaction test confirms document text is absent from ordinary logs.

## Demonstration

Use a configured ambiguous field with a citation, show why it was selected, display the model's evidence-grounded result, and then simulate endpoint failure to show a safe `UNCERTAIN` outcome.

## Rollback boundary

Reverting removes endpoint calls and LLM-specific UI. Deterministic validation continues to operate, and prior LLM results remain auditable.

## Progress statement

> Ambiguous values can now receive a source-grounded second check, with failures routed safely to review and no automatic correction.

## Definition of done

- Only eligible fields cause endpoint calls.
- Failure paths never create a pass.
- Extracted values remain immutable.
- [Progress tracker](PROGRESS_TRACKER.md) is updated.

