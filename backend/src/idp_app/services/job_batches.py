"""Shared helpers for submitting a set of documents as one Databricks Job run."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


def batch_idempotency_token(run_ids: Iterable[str]) -> str:
    """A stable token for one submission of a set of immutable runs.

    Run identifiers are freshly generated per attempt, so hashing them keeps retries distinct
    while making a duplicated submission of the same set idempotent. SHA-256 hex is exactly the
    64 characters the Jobs API allows.
    """
    joined = "|".join(sorted(run_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def encode_inputs(inputs: list[dict[str, Any]]) -> str:
    """Render for_each inputs as the compact JSON array the Jobs API expects."""
    return json.dumps(inputs, separators=(",", ":"))


@dataclass(frozen=True)
class BatchFailure:
    """One document that could not join a batch, reported without failing the others."""

    document_id: str
    code: str
    message: str
