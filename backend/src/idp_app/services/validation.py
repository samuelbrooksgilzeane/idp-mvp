"""Deterministic validation.

Two classes of check run over an immutable extraction run:

* Structural validators are generic. They read only the extraction contract that every schema
  shares - declared types, field policies, citations, confidence and provenance - so they work
  for any use case without configuration.
* Business rules are declarative. They come from the registered schema manifest as a closed set
  of parameterised rule types, so invoice arithmetic is configuration rather than code.

Validators only ever observe. They never edit an extracted value or approve a document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from idp_app.services.document_models import (
    DocumentRecord,
    ExtractedFieldRecord,
    ExtractionRunRecord,
    ParseRunRecord,
)
from idp_app.services.schema_models import (
    DocumentRule,
    ExtractField,
    FieldPolicy,
    SchemaRecord,
    rule_path_root,
)
from idp_app.services.viewer import normalise_box

VALIDATOR_VERSION = "1.0.0"

PASS = "PASS"
FAIL = "FAIL"
UNCERTAIN = "UNCERTAIN"
SKIPPED = "SKIPPED"

INFO = "INFO"
WARNING = "WARNING"
BLOCKING = "BLOCKING"

_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
_DATE_FORMATS = (
    "%d-%b-%Y",
    "%d %b %Y",
    "%d-%B-%Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
)


@dataclass(frozen=True)
class Observation:
    rule_id: str
    validator_type: str
    severity: str
    status: str
    message: str
    field_path: str | None = None
    actual_value: str | None = None
    expected_value: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class ValidationContext:
    """Everything the deterministic validators read. Assembled by the caller, never mutated."""

    document: DocumentRecord
    run: ExtractionRunRecord
    schema: SchemaRecord
    fields: list[ExtractedFieldRecord]
    parse: ParseRunRecord | None = None
    registered_schema_hash: str | None = None
    latest_schema_version: int | None = None
    latest_parse_run_id: str | None = None
    duplicate_document_ids: tuple[str, ...] = ()

    @property
    def by_path(self) -> dict[str, ExtractedFieldRecord]:
        return {field.field_path: field for field in self.fields}


def severity_for(policy: FieldPolicy | None) -> str:
    """Derive severity from declared risk, so policy drives blocking-ness rather than code."""
    if policy is None:
        return WARNING
    if policy.risk_tier == "high":
        return BLOCKING
    if policy.risk_tier == "medium":
        return WARNING
    return INFO


# --------------------------------------------------------------------------------------
# Structural validators
# --------------------------------------------------------------------------------------


def check_provenance(context: ValidationContext) -> list[Observation]:
    if context.run.status != "EXTRACTED":
        return [
            Observation(
                rule_id="provenance",
                validator_type="TECHNICAL",
                severity=BLOCKING,
                status=FAIL,
                message="Validation requires a successful extraction run.",
                actual_value=context.run.status,
                expected_value="EXTRACTED",
            )
        ]
    if context.parse is not None and context.parse.status != "SUCCESS":
        return [
            Observation(
                rule_id="provenance",
                validator_type="TECHNICAL",
                severity=BLOCKING,
                status=FAIL,
                message="The extraction references a parse attempt that did not succeed.",
                actual_value=context.parse.status,
                expected_value="SUCCESS",
            )
        ]
    return [
        Observation(
            rule_id="provenance",
            validator_type="TECHNICAL",
            severity=INFO,
            status=PASS,
            message="Extraction and parse provenance are intact.",
            evidence=f"extraction_run_id={context.run.extraction_run_id}",
        )
    ]


def check_schema_drift(context: ValidationContext) -> list[Observation]:
    """Integrity of the exact contract used, kept separate from whether it is the newest one."""
    registered = context.registered_schema_hash or context.schema.schema_hash
    if registered != context.run.schema_hash:
        # The same schema_id and version now hashes differently, so a registered contract
        # was altered underneath a completed extraction.
        observations = [
            Observation(
                rule_id="schema_drift",
                validator_type="TECHNICAL",
                severity=BLOCKING,
                status=FAIL,
                message=(
                    "The registered contract for this schema version no longer matches the "
                    "hash this extraction recorded."
                ),
                actual_value=context.run.schema_hash,
                expected_value=registered,
            )
        ]
    else:
        observations = [
            Observation(
                rule_id="schema_drift",
                validator_type="TECHNICAL",
                severity=INFO,
                status=PASS,
                message="The extraction schema version still matches its registered contract.",
                actual_value=context.run.schema_hash,
            )
        ]
    observations.extend(_schema_currency(context))
    return observations


def _schema_currency(context: ValidationContext) -> list[Observation]:
    """A superseded contract is worth surfacing, but it is not an integrity failure."""
    latest = context.latest_schema_version
    if latest is None:
        return []
    if latest > context.run.schema_version:
        return [
            Observation(
                rule_id="schema_version_currency",
                validator_type="TECHNICAL",
                severity=WARNING,
                status=UNCERTAIN,
                message=(
                    f"A newer production contract (version {latest}) is registered; this result "
                    f"was produced under version {context.run.schema_version}."
                ),
                actual_value=str(context.run.schema_version),
                expected_value=str(latest),
            )
        ]
    return [
        Observation(
            rule_id="schema_version_currency",
            validator_type="TECHNICAL",
            severity=INFO,
            status=PASS,
            message="The extraction used the newest registered production contract.",
            actual_value=str(context.run.schema_version),
        )
    ]


def check_parse_staleness(context: ValidationContext) -> list[Observation]:
    if context.latest_parse_run_id is None:
        return [
            Observation(
                rule_id="parse_staleness",
                validator_type="TECHNICAL",
                severity=WARNING,
                status=SKIPPED,
                message="No successful parse could be resolved for comparison.",
            )
        ]
    if context.latest_parse_run_id != context.run.parse_run_id:
        return [
            Observation(
                rule_id="parse_staleness",
                validator_type="TECHNICAL",
                severity=WARNING,
                status=FAIL,
                message="The document was re-parsed after this extraction, so the result is stale.",
                actual_value=context.run.parse_run_id,
                expected_value=context.latest_parse_run_id,
            )
        ]
    return [
        Observation(
            rule_id="parse_staleness",
            validator_type="TECHNICAL",
            severity=INFO,
            status=PASS,
            message="The extraction used the latest successful parse.",
        )
    ]


def check_cast_integrity(context: ValidationContext) -> list[Observation]:
    """A returned value must be coercible to the type the registered schema declares."""
    observations: list[Observation] = []
    for path, definition in context.schema.ai_extract_schema.items():
        field = context.by_path.get(path)
        policy = context.schema.field_policies.get(path)
        if field is None or field.value is None:
            continue
        error = _coercion_error(field, definition, policy)
        if error is None:
            observations.append(
                Observation(
                    rule_id="cast_integrity",
                    validator_type="TECHNICAL",
                    severity=INFO,
                    status=PASS,
                    message=f"{path} matches its declared type.",
                    field_path=path,
                    actual_value=field.value_string,
                    expected_value=definition.type,
                )
            )
        else:
            observations.append(
                Observation(
                    rule_id="cast_integrity",
                    validator_type="TECHNICAL",
                    severity=severity_for(policy),
                    status=FAIL,
                    message=error,
                    field_path=path,
                    actual_value=field.value_string,
                    expected_value=definition.type,
                )
            )
    return observations


def _coercion_error(
    field: ExtractedFieldRecord, definition: ExtractField, policy: FieldPolicy | None
) -> str | None:
    raw = field.value_string
    if definition.type in {"number", "integer"}:
        if _decimal_or_none(field.value) is None:
            return f"{field.field_path} is declared {definition.type} but did not cast."
    elif definition.type == "boolean":
        if not isinstance(field.value, bool):
            return f"{field.field_path} is declared boolean but did not cast."
    elif definition.type == "enum":
        labels = definition.labels or []
        if raw is not None and raw not in labels:
            return f"{field.field_path} is not one of its declared enum labels."
    if policy is not None and policy.semantic_type is not None and raw is not None:
        if policy.semantic_type == "date" and parse_semantic_date(raw) is None:
            return f"{field.field_path} is declared a date but could not be interpreted."
        if policy.semantic_type == "currency_code" and _CURRENCY_CODE.fullmatch(raw) is None:
            return f"{field.field_path} is declared a currency code but is not three letters."
    return None


def check_citations(context: ValidationContext) -> list[Observation]:
    observations: list[Observation] = []
    page_count = context.parse.page_count if context.parse else None
    for path, policy in context.schema.field_policies.items():
        field = context.by_path.get(path)
        if field is None or field.value is None:
            # A field with no value cannot carry evidence; the required-field rule owns this case.
            observations.append(
                Observation(
                    rule_id="citation_presence",
                    validator_type="TECHNICAL",
                    severity=INFO,
                    status=SKIPPED,
                    message=f"{path} returned no value, so citation evidence does not apply.",
                    field_path=path,
                )
            )
            continue
        boxes = [box for citation in field.citations for box in citation.get("bbox", [])]
        if policy.citation_required and not boxes:
            observations.append(
                Observation(
                    rule_id="citation_presence",
                    validator_type="TECHNICAL",
                    severity=severity_for(policy),
                    status=FAIL,
                    message=f"{path} requires source evidence but no citation was returned.",
                    field_path=path,
                    actual_value=field.value_string,
                )
            )
            continue
        if boxes:
            observations.append(
                Observation(
                    rule_id="citation_presence",
                    validator_type="TECHNICAL",
                    severity=INFO,
                    status=PASS,
                    message=f"{path} is supported by {len(boxes)} citation region(s).",
                    field_path=path,
                )
            )
        observations.extend(_geometry_observations(path, boxes, page_count, policy))
    return observations


def _geometry_observations(
    path: str, boxes: list[dict[str, Any]], page_count: int | None, policy: FieldPolicy
) -> list[Observation]:
    problems: list[str] = []
    for box in boxes:
        page_id = box.get("page_id")
        if not isinstance(page_id, int) or page_id < 0:
            problems.append("citation references an invalid page")
            continue
        if page_count is not None and page_id >= page_count:
            problems.append(f"citation references page {page_id} beyond the parsed page count")
        # Reuse the viewer's canonical convention so rectangles and polygons are read the same
        # way the overlay reads them.
        if normalise_box(box) is None:
            problems.append("citation box is malformed or has non-positive area")
    if not problems:
        return []
    return [
        Observation(
            rule_id="citation_geometry",
            validator_type="TECHNICAL",
            severity=severity_for(policy),
            status=FAIL,
            message=f"{path} has unusable citation geometry: {'; '.join(sorted(set(problems)))}.",
            field_path=path,
        )
    ]


def check_grounding(context: ValidationContext) -> list[Observation]:
    """A returned value should appear in the retained document text."""
    text = context.parse.document_text if context.parse else None
    observations: list[Observation] = []
    for path, definition in context.schema.ai_extract_schema.items():
        field = context.by_path.get(path)
        policy = context.schema.field_policies.get(path)
        if field is None or field.value is None or not field.value_string:
            continue
        if not text:
            observations.append(
                Observation(
                    rule_id="grounding",
                    validator_type="TECHNICAL",
                    severity=INFO,
                    status=SKIPPED,
                    message=f"No document text was retained, so {path} cannot be grounded.",
                    field_path=path,
                )
            )
            continue
        if _appears_in(field.value_string, text, definition.type):
            observations.append(
                Observation(
                    rule_id="grounding",
                    validator_type="TECHNICAL",
                    severity=INFO,
                    status=PASS,
                    message=f"{path} appears verbatim in the parsed document text.",
                    field_path=path,
                    actual_value=field.value_string,
                )
            )
        else:
            observations.append(
                Observation(
                    rule_id="grounding",
                    validator_type="TECHNICAL",
                    # Absence from the text is a strong signal but not proof: the parser may
                    # normalise characters. Record uncertainty rather than asserting an error.
                    severity=severity_for(policy),
                    status=UNCERTAIN,
                    message=(
                        f"{path} could not be located in the parsed document text and needs "
                        "human confirmation."
                    ),
                    field_path=path,
                    actual_value=field.value_string,
                )
            )
    return observations


def _appears_in(value: str, text: str, declared_type: str) -> bool:
    haystack = text.casefold()
    needle = value.strip().casefold()
    if not needle:
        return False
    if needle in haystack:
        return True
    if declared_type in {"number", "integer"}:
        amount = _decimal_or_none(value)
        if amount is not None:
            # Compare numerically so 888.5 and 888.50 both match the printed figure.
            for candidate in {
                f"{amount:f}".rstrip("0").rstrip("."),
                f"{amount:.2f}",
                f"{amount:,.2f}",
            }:
                if candidate and candidate.casefold() in haystack:
                    return True
    return False


def check_confidence(context: ValidationContext) -> list[Observation]:
    observations: list[Observation] = []
    for path, policy in context.schema.field_policies.items():
        field = context.by_path.get(path)
        if field is None or field.value is None:
            continue
        if field.confidence_score is None:
            observations.append(
                Observation(
                    rule_id="confidence_threshold",
                    validator_type="TECHNICAL",
                    severity=INFO,
                    status=UNCERTAIN,
                    message=f"{path} returned no confidence score.",
                    field_path=path,
                    expected_value=f">= {policy.confidence_threshold}",
                )
            )
            continue
        if field.confidence_score < policy.confidence_threshold:
            observations.append(
                Observation(
                    rule_id="confidence_threshold",
                    validator_type="TECHNICAL",
                    # Low confidence is a review signal, never proof of an incorrect value.
                    severity=BLOCKING if policy.risk_tier == "high" else WARNING,
                    status=UNCERTAIN,
                    message=(
                        f"{path} was returned below its configured confidence threshold and "
                        "needs review."
                    ),
                    field_path=path,
                    actual_value=f"{field.confidence_score:.4f}",
                    expected_value=f">= {policy.confidence_threshold}",
                )
            )
        else:
            observations.append(
                Observation(
                    rule_id="confidence_threshold",
                    validator_type="TECHNICAL",
                    severity=INFO,
                    status=PASS,
                    message=f"{path} met its configured confidence threshold.",
                    field_path=path,
                    actual_value=f"{field.confidence_score:.4f}",
                    expected_value=f">= {policy.confidence_threshold}",
                )
            )
    return observations


def check_duplicates(context: ValidationContext) -> list[Observation]:
    """File-level duplicates are rejected at upload, so this reports the business duplicate."""
    if not context.duplicate_document_ids:
        return [
            Observation(
                rule_id="duplicate_document",
                validator_type="TECHNICAL",
                severity=INFO,
                status=PASS,
                message="No other document carries the same seller and invoice number.",
            )
        ]
    return [
        Observation(
            rule_id="duplicate_document",
            validator_type="TECHNICAL",
            severity=BLOCKING,
            status=FAIL,
            message=(
                "Another registered document reports the same seller and invoice number, "
                "which may be a duplicate submission."
            ),
            evidence=", ".join(context.duplicate_document_ids[:5]),
        )
    ]


def check_field_coverage(context: ValidationContext) -> list[Observation]:
    declared = len(context.schema.ai_extract_schema)
    returned = sum(
        1
        for path in context.schema.ai_extract_schema
        if (field := context.by_path.get(path)) is not None and field.value is not None
    )
    return [
        Observation(
            rule_id="field_coverage",
            validator_type="TECHNICAL",
            severity=INFO,
            status=PASS if returned == declared else UNCERTAIN,
            message=f"{returned} of {declared} declared fields returned a value.",
            actual_value=str(returned),
            expected_value=str(declared),
        )
    ]


STRUCTURAL_VALIDATORS = (
    check_provenance,
    check_schema_drift,
    check_parse_staleness,
    check_cast_integrity,
    check_citations,
    check_grounding,
    check_confidence,
    check_duplicates,
    check_field_coverage,
)


# --------------------------------------------------------------------------------------
# Declarative business rules
# --------------------------------------------------------------------------------------


def evaluate_rule(rule: DocumentRule, context: ValidationContext) -> list[Observation]:
    handlers = {
        "required_fields": _rule_required_fields,
        "arithmetic_reconciliation": _rule_arithmetic,
        "allowed_values": _rule_allowed_values,
        "range": _rule_range,
        "format": _rule_format,
        "comparison": _rule_comparison,
    }
    handler = handlers.get(rule.rule_type)
    if handler is None:
        return [_rule_observation(rule, context, SKIPPED, "Rule type is not supported.")]
    return handler(rule, context)


def _rule_severity(rule: DocumentRule, context: ValidationContext) -> str:
    if rule.severity is not None:
        return rule.severity
    tiers = [
        context.schema.field_policies[rule_path_root(path)].risk_tier
        for path in rule.field_paths
        if rule_path_root(path) in context.schema.field_policies
    ]
    if "high" in tiers:
        return BLOCKING
    if "medium" in tiers:
        return WARNING
    return INFO


def _rule_observation(
    rule: DocumentRule,
    context: ValidationContext,
    status: str,
    message: str,
    *,
    field_path: str | None = None,
    actual_value: str | None = None,
    expected_value: str | None = None,
) -> Observation:
    return Observation(
        rule_id=rule.rule_id,
        validator_type="BUSINESS",
        severity=_rule_severity(rule, context) if status != PASS else INFO,
        status=status,
        message=message,
        field_path=field_path,
        actual_value=actual_value,
        expected_value=expected_value,
    )


def _rule_required_fields(rule: DocumentRule, context: ValidationContext) -> list[Observation]:
    missing = [
        path
        for path in rule.field_paths
        if (field := context.by_path.get(path)) is None or field.value is None
    ]
    if missing:
        return [
            _rule_observation(
                rule,
                context,
                FAIL,
                f"Required values are missing: {', '.join(sorted(missing))}.",
                expected_value=", ".join(rule.field_paths),
            )
        ]
    return [_rule_observation(rule, context, PASS, "All required values are present.")]


def _rule_arithmetic(rule: DocumentRule, context: ValidationContext) -> list[Observation]:
    if not rule.terms or not rule.target:
        # Reconciliation needs explicit signed terms. A manifest registered before those were
        # available is reported as skipped rather than guessed at.
        return [
            _rule_observation(
                rule,
                context,
                SKIPPED,
                "This reconciliation rule does not declare signed terms and a target.",
            )
        ]
    total = Decimal("0")
    missing: list[str] = []
    for term in rule.terms:
        amount = _decimal_or_none(_value_at(term.field_path, context))
        if amount is None:
            missing.append(term.field_path)
            continue
        total += amount if term.sign == "+" else -amount
    target = _decimal_or_none(_value_at(rule.target, context))
    if target is None:
        missing.append(rule.target)
    if missing:
        # Absent inputs can never produce a pass.
        return [
            _rule_observation(
                rule,
                context,
                UNCERTAIN,
                (
                    "Reconciliation could not be evaluated because these values were not "
                    f"returned: {', '.join(sorted(set(missing)))}."
                ),
                expected_value=rule.target,
            )
        ]
    assert target is not None
    tolerance = Decimal(str(rule.tolerance if rule.tolerance is not None else 0))
    delta = (total - target).copy_abs()
    if delta <= tolerance:
        return [
            _rule_observation(
                rule,
                context,
                PASS,
                f"Reconciliation balances within a tolerance of {tolerance}.",
                field_path=rule.target,
                actual_value=str(target),
                expected_value=str(total),
            )
        ]
    return [
        _rule_observation(
            rule,
            context,
            FAIL,
            f"Reconciliation is out by {delta}, beyond the configured tolerance of {tolerance}.",
            field_path=rule.target,
            actual_value=str(target),
            expected_value=str(total),
        )
    ]


def _rule_allowed_values(rule: DocumentRule, context: ValidationContext) -> list[Observation]:
    allowed = rule.allowed_values or []
    observations: list[Observation] = []
    for path in rule.field_paths:
        raw = _raw_at(path, context)
        if raw is None:
            observations.append(
                _rule_observation(
                    rule, context, SKIPPED, f"{path} returned no value to check.", field_path=path
                )
            )
        elif raw in allowed:
            observations.append(
                _rule_observation(
                    rule,
                    context,
                    PASS,
                    f"{path} is an allowed value.",
                    field_path=path,
                    actual_value=raw,
                )
            )
        else:
            observations.append(
                _rule_observation(
                    rule,
                    context,
                    FAIL,
                    f"{path} is not in the configured allowed list.",
                    field_path=path,
                    actual_value=raw,
                    expected_value=", ".join(allowed),
                )
            )
    return observations


def _rule_range(rule: DocumentRule, context: ValidationContext) -> list[Observation]:
    observations: list[Observation] = []
    expected = f"{rule.minimum if rule.minimum is not None else '-inf'}"
    expected += f" .. {rule.maximum if rule.maximum is not None else '+inf'}"
    for path in rule.field_paths:
        amount = _decimal_or_none(_value_at(path, context))
        if amount is None:
            observations.append(
                _rule_observation(
                    rule,
                    context,
                    SKIPPED,
                    f"{path} returned no numeric value to check.",
                    field_path=path,
                )
            )
            continue
        below = rule.minimum is not None and amount < Decimal(str(rule.minimum))
        above = rule.maximum is not None and amount > Decimal(str(rule.maximum))
        observations.append(
            _rule_observation(
                rule,
                context,
                FAIL if below or above else PASS,
                f"{path} is outside its configured range."
                if below or above
                else f"{path} is within its configured range.",
                field_path=path,
                actual_value=str(amount),
                expected_value=expected,
            )
        )
    return observations


def _rule_format(rule: DocumentRule, context: ValidationContext) -> list[Observation]:
    pattern = re.compile(rule.pattern or "")
    observations: list[Observation] = []
    for path in rule.field_paths:
        raw = _raw_at(path, context)
        if raw is None:
            observations.append(
                _rule_observation(
                    rule, context, SKIPPED, f"{path} returned no value to check.", field_path=path
                )
            )
            continue
        observations.append(
            _rule_observation(
                rule,
                context,
                PASS if pattern.fullmatch(raw) else FAIL,
                f"{path} matches its configured format."
                if pattern.fullmatch(raw)
                else f"{path} does not match its configured format.",
                field_path=path,
                actual_value=raw,
                expected_value=rule.pattern,
            )
        )
    return observations


_COMPARATORS = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


def _rule_comparison(rule: DocumentRule, context: ValidationContext) -> list[Observation]:
    compare = _COMPARATORS[rule.comparator] if rule.comparator else None
    if compare is None or rule.compare_to is None:
        return [_rule_observation(rule, context, SKIPPED, "Comparison rule is not configured.")]
    other = _decimal_or_none(_value_at(rule.compare_to, context))
    observations: list[Observation] = []
    for path in rule.field_paths:
        amount = _decimal_or_none(_value_at(path, context))
        if amount is None or other is None:
            observations.append(
                _rule_observation(
                    rule,
                    context,
                    UNCERTAIN,
                    f"{path} could not be compared to {rule.compare_to} because a value is absent.",
                    field_path=path,
                )
            )
            continue
        observations.append(
            _rule_observation(
                rule,
                context,
                PASS if compare(amount, other) else FAIL,
                f"{path} satisfies {rule.comparator} {rule.compare_to}."
                if compare(amount, other)
                else f"{path} does not satisfy {rule.comparator} {rule.compare_to}.",
                field_path=path,
                actual_value=str(amount),
                expected_value=f"{rule.comparator} {other}",
            )
        )
    return observations


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------


def run_validators(context: ValidationContext) -> list[Observation]:
    """Run every structural validator and every registered rule, isolating validator failures."""
    observations: list[Observation] = []
    for validator in STRUCTURAL_VALIDATORS:
        observations.extend(
            _safely(validator.__name__, lambda v=validator: v(context))
        )
    for rule in context.schema.document_rules:
        observations.extend(_safely(rule.rule_id, lambda r=rule: evaluate_rule(r, context)))
    return observations


def _safely(rule_id: str, run: Any) -> list[Observation]:
    try:
        return list(run())
    except Exception as error:  # noqa: BLE001 - a validator defect must stay auditable
        return [
            Observation(
                rule_id=rule_id,
                validator_type="TECHNICAL",
                severity=BLOCKING,
                status=UNCERTAIN,
                message=f"The validator did not complete: {type(error).__name__}.",
                evidence=str(error)[:400],
            )
        ]


def decide_document_status(observations: list[Observation]) -> str:
    """Blocking failures and unresolved blocking uncertainty both require review."""
    for observation in observations:
        if observation.severity == BLOCKING and observation.status in {FAIL, UNCERTAIN}:
            return "REVIEW_REQUIRED"
    return "VALIDATED_PASS"


# --------------------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------------------


def _value_at(path: str, context: ValidationContext) -> object:
    field = context.by_path.get(path)
    return field.value if field else None


def _raw_at(path: str, context: ValidationContext) -> str | None:
    field = context.by_path.get(path)
    if field is None or field.value is None:
        return None
    return field.value_string


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_semantic_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None
