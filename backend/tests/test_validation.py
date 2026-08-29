"""Deterministic validation: structural validators and declarative business rules."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from idp_app.services.document_models import (
    DocumentRecord,
    ExtractedFieldRecord,
    ExtractionRunRecord,
    ParseRunRecord,
)
from idp_app.services.schema_models import SchemaManifest, SchemaRecord, schema_leaves
from idp_app.services.schemas import load_source_manifests
from idp_app.services.validation import (
    BLOCKING,
    FAIL,
    PASS,
    SKIPPED,
    UNCERTAIN,
    ValidationContext,
    decide_document_status,
    run_validators,
)

V1_HASH = "b02b3c20d69e7f77ed76e45337107d4995aafab5ee411ab4f2e73b166876c640"
V2_HASH = "63e45630339a8783077968bf3a0c6ba185011edf749ec6c57e9bf1b3b252a05f"

BALANCED = {
    "invoice_number": "INV-1042",
    "invoice_date": "28-Jul-2011",
    "seller_name": "Acme Supplies Ltd",
    "subtotal": 100.00,
    "discount": 5.00,
    "tax": 19.00,
    "total": 114.00,
    "currency": "GBP",
}

DOCUMENT_TEXT = (
    "Seller: Acme Supplies Ltd\nInvoice Number: INV-1042\nInvoice Date: 28-Jul-2011\n"
    "Subtotal: 100.00\nDiscount: 5.00\nTax: 19.00\nTotal: 114.00\nCurrency: GBP"
)


def _manifest(version: int) -> SchemaManifest:
    return next(m for m in load_source_manifests() if m.schema_version == version)


def _schema(version: int = 2) -> SchemaRecord:
    manifest = _manifest(version)
    return SchemaRecord(
        schema_id=manifest.schema_id,
        schema_version=manifest.schema_version,
        display_name=manifest.display_name,
        use_case=manifest.use_case,
        ai_extract_schema=manifest.ai_extract_schema,
        instructions=manifest.instructions,
        field_policies=manifest.field_policies,
        document_rules=manifest.document_rules,
        schema_hash=manifest.schema_hash,
        status="PRODUCTION",
        created_by="test@example.com",
        created_at=datetime.now(UTC),
    )


def _fields(values: dict[str, Any], *, cited: bool = True, confidence: float = 0.99) -> list[
    ExtractedFieldRecord
]:
    schema = _schema().ai_extract_schema
    records = []
    for path, definition in schema.items():
        value = values.get(path)
        citations = (
            [{"id": 0, "bbox": [{"coord": [10, 20, 120, 60], "page_id": 0}]}]
            if cited and value is not None
            else []
        )
        records.append(
            ExtractedFieldRecord(
                extraction_run_id="run-1",
                document_id="doc-1",
                field_path=path,
                field_type=definition.type,
                value=value,
                value_string=None if value is None else str(value),
                confidence_score=None if value is None else confidence,
                citation_ids=[0] if citations else [],
                citations=citations,
                extraction_error=None,
            )
        )
    return records


def _context(
    values: dict[str, Any],
    *,
    version: int = 2,
    cited: bool = True,
    confidence: float = 0.99,
    text: str | None = DOCUMENT_TEXT,
    duplicates: tuple[str, ...] = (),
    latest_parse: str | None = "parse-1",
) -> ValidationContext:
    schema = _schema(version)
    now = datetime.now(UTC)
    document = DocumentRecord(
        document_id="doc-1", case_id=None, template_id="invoice_v1", use_case="invoice",
        source_path="/Volumes/c/s/v/incoming/a.pdf", file_name="a.pdf", file_size=10,
        content_sha256="a" * 64, selected_schema_id="invoice", selected_schema_version=version,
        status="EXTRACTED", uploaded_by="t@example.com", uploaded_at=now, updated_at=now,
    )
    run = ExtractionRunRecord(
        extraction_run_id="run-1", document_id="doc-1", parse_run_id="parse-1",
        schema_id="invoice", schema_version=version, schema_hash=schema.schema_hash,
        extractor_version="2.1", options={}, ai_result=None, error_message=None,
        status="EXTRACTED", requested_by="t@example.com", job_run_id=1,
        started_at=now, completed_at=now,
    )
    parse = ParseRunRecord(
        parse_run_id="parse-1", document_id="doc-1", content_sha256="a" * 64, parser_version="2.0",
        parsed=None, document_text=text, page_count=1, page_image_root="/Volumes/c/s/v/p",
        parse_error=None, status="SUCCESS", requested_by="t@example.com", job_run_id=1,
        started_at=now, completed_at=now,
    )
    return ValidationContext(
        document=document, run=run, schema=schema,
        fields=_fields(values, cited=cited, confidence=confidence), parse=parse,
        registered_schema_hash=schema.schema_hash, latest_parse_run_id=latest_parse,
        duplicate_document_ids=duplicates,
    )


def _by_rule(observations, rule_id: str):
    return [o for o in observations if o.rule_id == rule_id]


def test_invoice_v1_hash_is_immutable() -> None:
    """Commit 7/8 evidence and the registry conflict check depend on this exact hash."""
    assert _manifest(1).schema_hash == V1_HASH


def test_balanced_invoice_reconciles_and_passes() -> None:
    observations = run_validators(_context(BALANCED))
    reconciliation = _by_rule(observations, "invoice_total_reconciliation")
    assert [o.status for o in reconciliation] == [PASS]
    assert decide_document_status(observations) == "VALIDATED_PASS"


@pytest.mark.parametrize(
    ("total", "expected"),
    [(114.00, PASS), (114.01, PASS), (113.99, PASS), (114.50, FAIL), (100.00, FAIL)],
)
def test_reconciliation_respects_configured_tolerance(total: float, expected: str) -> None:
    observations = run_validators(_context({**BALANCED, "total": total}))
    assert _by_rule(observations, "invoice_total_reconciliation")[0].status == expected


@pytest.mark.parametrize("absent", ["subtotal", "discount", "tax", "total"])
def test_missing_inputs_are_never_a_pass(absent: str) -> None:
    observations = run_validators(_context({**BALANCED, absent: None}))
    result = _by_rule(observations, "invoice_total_reconciliation")[0]
    assert result.status == UNCERTAIN
    assert absent in result.message


def test_out_of_tolerance_total_requires_review() -> None:
    observations = run_validators(_context({**BALANCED, "total": 999.00}))
    failure = _by_rule(observations, "invoice_total_reconciliation")[0]
    assert failure.status == FAIL and failure.severity == BLOCKING
    assert decide_document_status(observations) == "REVIEW_REQUIRED"


def test_negative_total_and_bad_currency_are_blocked() -> None:
    observations = run_validators(
        _context({**BALANCED, "total": -114.00, "currency": "pounds"})
    )
    assert _by_rule(observations, "invoice_total_not_negative")[0].status == FAIL
    assert _by_rule(observations, "invoice_currency_format")[0].status == FAIL
    assert decide_document_status(observations) == "REVIEW_REQUIRED"


def test_discount_exceeding_subtotal_fails_comparison() -> None:
    observations = run_validators(_context({**BALANCED, "discount": 500.00}))
    assert _by_rule(observations, "discount_within_subtotal")[0].status == FAIL


def test_required_fields_rule_reports_absent_identity() -> None:
    observations = run_validators(_context({**BALANCED, "invoice_number": None}))
    result = _by_rule(observations, "required_invoice_identity")[0]
    assert result.status == FAIL and "invoice_number" in result.message


def test_grounding_flags_a_value_absent_from_the_document_text() -> None:
    observations = run_validators(_context({**BALANCED, "seller_name": "Never Printed Ltd"}))
    grounded = {o.field_path: o for o in _by_rule(observations, "grounding")}
    assert grounded["seller_name"].status == UNCERTAIN
    assert grounded["invoice_number"].status == PASS


def test_grounding_matches_numeric_formatting_differences() -> None:
    observations = run_validators(_context({**BALANCED, "total": 114.0}))
    grounded = {o.field_path: o for o in _by_rule(observations, "grounding")}
    assert grounded["total"].status == PASS


def test_cast_integrity_flags_an_uninterpretable_declared_date() -> None:
    observations = run_validators(_context({**BALANCED, "invoice_date": "last Thursday"}))
    casts = {o.field_path: o for o in _by_rule(observations, "cast_integrity")}
    assert casts["invoice_date"].status == FAIL
    assert decide_document_status(observations) == "REVIEW_REQUIRED"


def test_missing_citation_on_a_required_field_is_reported() -> None:
    observations = run_validators(_context(BALANCED, cited=False))
    citations = {o.field_path: o for o in _by_rule(observations, "citation_presence")}
    assert citations["total"].status == FAIL


def test_absent_optional_field_skips_citation_rather_than_failing() -> None:
    observations = run_validators(_context({**BALANCED, "subtotal": None}))
    citations = {o.field_path: o for o in _by_rule(observations, "citation_presence")}
    assert citations["subtotal"].status == SKIPPED


def test_low_confidence_is_uncertain_not_a_failure() -> None:
    observations = run_validators(_context(BALANCED, confidence=0.10))
    results = _by_rule(observations, "confidence_threshold")
    assert results and all(o.status == UNCERTAIN for o in results)
    assert all(o.status != FAIL for o in results)


def test_stale_parse_and_business_duplicate_are_reported() -> None:
    observations = run_validators(
        _context(BALANCED, duplicates=("doc-9",), latest_parse="parse-2")
    )
    assert _by_rule(observations, "parse_staleness")[0].status == FAIL
    duplicate = _by_rule(observations, "duplicate_document")[0]
    assert duplicate.status == FAIL and "doc-9" in (duplicate.evidence or "")


def test_v1_reconciliation_without_terms_is_skipped_not_guessed() -> None:
    """v1 declares no signed terms, so the engine refuses to infer them."""
    observations = run_validators(_context(BALANCED, version=1))
    assert _by_rule(observations, "invoice_total_reconciliation")[0].status == SKIPPED


def test_validators_never_mutate_extraction_data() -> None:
    context = _context(BALANCED)
    before = [(f.field_path, f.value, f.confidence_score) for f in context.fields]
    run_validators(context)
    assert [(f.field_path, f.value, f.confidence_score) for f in context.fields] == before


def test_a_validator_failure_becomes_an_auditable_non_pass() -> None:
    context = _context(BALANCED)
    # Replace the policy mapping with something that cannot be looked up at all.
    object.__setattr__(context.schema, "field_policies", ["not-a-mapping"])
    observations = run_validators(context)
    assert any(o.status == UNCERTAIN and "did not complete" in o.message for o in observations)


@pytest.mark.parametrize(
    "rule",
    [
        {"rule_id": "bad", "rule_type": "allowed_values", "description": "d",
         "field_paths": ["currency"]},
        {"rule_id": "bad", "rule_type": "range", "description": "d", "field_paths": ["total"]},
        {"rule_id": "bad", "rule_type": "format", "description": "d", "field_paths": ["currency"]},
        {"rule_id": "bad", "rule_type": "comparison", "description": "d",
         "field_paths": ["discount"]},
        {"rule_id": "bad", "rule_type": "format", "description": "d",
         "field_paths": ["currency"], "pattern": "([unclosed"},
        {"rule_id": "bad", "rule_type": "required_fields", "description": "d",
         "field_paths": ["not_a_field"]},
    ],
)
def test_malformed_rules_are_rejected_at_registration(rule: dict[str, Any]) -> None:
    payload = _manifest(2).model_dump(mode="json", exclude_none=True)
    payload["document_rules"] = [rule]
    with pytest.raises(ValidationError):
        SchemaManifest.model_validate(payload)


def test_an_altered_registered_contract_is_an_integrity_failure() -> None:
    """The same schema version hashing differently means the contract moved underneath us."""
    context = _context(BALANCED)
    object.__setattr__(context, "registered_schema_hash", "f" * 64)
    observations = run_validators(context)
    drift = _by_rule(observations, "schema_drift")[0]
    assert drift.status == FAIL and drift.severity == BLOCKING
    assert decide_document_status(observations) == "REVIEW_REQUIRED"


def test_a_superseded_contract_is_surfaced_without_failing_integrity() -> None:
    """Using v1 while v2 exists is worth flagging, but it is not tampering."""
    context = _context(BALANCED, version=1)
    object.__setattr__(context, "latest_schema_version", 2)
    observations = run_validators(context)
    assert _by_rule(observations, "schema_drift")[0].status == PASS
    currency = _by_rule(observations, "schema_version_currency")[0]
    assert currency.status == UNCERTAIN and currency.severity != BLOCKING
    assert "version 2" in currency.message


def test_newest_contract_reports_currency_pass() -> None:
    context = _context(BALANCED, version=2)
    object.__setattr__(context, "latest_schema_version", 2)
    observations = run_validators(context)
    assert _by_rule(observations, "schema_version_currency")[0].status == PASS


# --------------------------------------------------------------------------------------
# Line items: recursive contracts and aggregate reconciliation
# --------------------------------------------------------------------------------------

LINES = [
    {"description": "Widget A", "quantity": 3.0, "unit_price": 91.65, "tax": 0.0, "amount": 274.95},
    {"description": "Widget B", "quantity": 2.0, "unit_price": 33.01, "tax": 0.0, "amount": 66.02},
]
LINE_TOTAL = Decimal("340.97")


def _line_fields(lines: list[dict[str, Any]]) -> list[ExtractedFieldRecord]:
    schema = _schema(3).ai_extract_schema["line_items"]
    assert schema.items is not None and schema.items.properties is not None
    records: list[ExtractedFieldRecord] = []
    for index, line in enumerate(lines):
        for leaf, definition in schema.items.properties.items():
            value = line.get(leaf)
            records.append(
                ExtractedFieldRecord(
                    extraction_run_id="run-1",
                    document_id="doc-1",
                    field_path=f"line_items[{index}].{leaf}",
                    field_type=definition.type,
                    value=value,
                    value_string=None if value is None else str(value),
                    confidence_score=None if value is None else 0.99,
                    citation_ids=[0] if value is not None else [],
                    citations=(
                        [{"id": 0, "bbox": [{"coord": [10, 20, 120, 60], "page_id": 0}]}]
                        if value is not None
                        else []
                    ),
                    extraction_error=None,
                )
            )
    return records


def _line_context(
    header: dict[str, Any], lines: list[dict[str, Any]]
) -> ValidationContext:
    context = _context(header, version=3)
    object.__setattr__(context, "fields", context.fields + _line_fields(lines))
    return context


def test_invoice_v3_declares_nested_leaves_and_keeps_earlier_hashes() -> None:
    assert _manifest(1).schema_hash == V1_HASH
    assert _manifest(2).schema_hash == V2_HASH
    leaves = dict(schema_leaves(_manifest(3).ai_extract_schema))
    assert "line_items[*].amount" in leaves
    assert leaves["line_items[*].amount"].type == "number"
    assert set(_manifest(3).field_policies) == set(leaves)


def test_line_items_reconcile_to_total_when_balanced() -> None:
    # sum(amount) - discount + sum(line tax) = 340.97 - 0.00 + 0.00
    header = {**BALANCED, "discount": 0.0, "total": float(LINE_TOTAL)}
    observations = run_validators(_line_context(header, LINES))
    result = _by_rule(observations, "line_items_reconcile_to_total")[0]
    assert result.status == PASS


def test_line_items_reconcile_to_total_fails_when_out_of_tolerance() -> None:
    header = {**BALANCED, "discount": 0.0, "total": 999.00}
    observations = run_validators(_line_context(header, LINES))
    result = _by_rule(observations, "line_items_reconcile_to_total")[0]
    assert result.status == FAIL and result.severity == BLOCKING
    assert result.expected_value == str(LINE_TOTAL)
    assert decide_document_status(observations) == "REVIEW_REQUIRED"


def test_line_sum_is_uncertain_when_subtotal_is_absent() -> None:
    observations = run_validators(_line_context({**BALANCED, "subtotal": None}, LINES))
    result = _by_rule(observations, "line_items_sum_to_subtotal")[0]
    assert result.status == UNCERTAIN and "subtotal" in result.message


def test_line_sum_passes_against_a_stated_subtotal() -> None:
    observations = run_validators(
        _line_context({**BALANCED, "subtotal": float(LINE_TOTAL)}, LINES)
    )
    assert _by_rule(observations, "line_items_sum_to_subtotal")[0].status == PASS


def test_absent_line_items_are_uncertain_never_an_implicit_zero() -> None:
    """No returned lines must not let an aggregate satisfy a calculation."""
    observations = run_validators(_line_context({**BALANCED, "total": 0.0}, []))
    for rule_id in ("line_items_reconcile_to_total", "line_items_sum_to_subtotal"):
        result = _by_rule(observations, rule_id)[0]
        assert result.status == UNCERTAIN
        assert result.status != PASS


def test_nested_leaves_resolve_their_wildcard_policy() -> None:
    """A nested instance must pick up the policy registered under its wildcard path."""
    context = _line_context(BALANCED, LINES)
    assert context.policy_for("line_items[1].amount") is not None
    assert context.policy_for("line_items[1].amount") == (
        context.schema.field_policies["line_items[*].amount"]
    )
    observations = run_validators(context)
    confidence_paths = {
        o.field_path for o in _by_rule(observations, "confidence_threshold")
    }
    assert "line_items[0].amount" in confidence_paths
    coverage = _by_rule(observations, "field_coverage")[0]
    assert coverage.expected_value == str(len(context.leaves))
