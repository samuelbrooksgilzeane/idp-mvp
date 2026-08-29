import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from idp_app.core.config import Settings
from idp_app.main import create_app
from idp_app.services.schema_models import SchemaManifest
from idp_app.services.schema_registry import (
    SchemaVersionConflictError,
    SQLiteSchemaRepository,
)
from idp_app.services.schemas import load_manifest, manifest_directory


@pytest.fixture
def manifest() -> SchemaManifest:
    return load_manifest(manifest_directory() / "invoice_v1.json")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(Settings(_env_file=None, local_data_dir=tmp_path / "local"))
    return TestClient(app)


def test_invoice_manifest_contains_the_governed_extraction_contract(
    manifest: SchemaManifest,
) -> None:
    assert manifest.schema_id == "invoice"
    assert manifest.schema_version == 1
    assert manifest.status == "PRODUCTION"
    assert list(manifest.ai_extract_schema) == [
        "invoice_number",
        "invoice_date",
        "seller_name",
        "subtotal",
        "discount",
        "tax",
        "total",
        "currency",
    ]
    assert "Do not infer, calculate" in manifest.instructions
    assert all(field.description for field in manifest.ai_extract_schema.values())
    assert set(manifest.field_policies) == set(manifest.ai_extract_schema)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload["ai_extract_schema"]["total"].update({"type": "decimal"}),
        lambda payload: payload["field_policies"].pop("currency"),
        lambda payload: payload["document_rules"][0]["field_paths"].append("unknown"),
    ],
)
def test_invalid_manifest_variants_are_rejected(
    manifest: SchemaManifest,
    mutation,
) -> None:
    payload = manifest.model_dump(mode="json", exclude_none=True)
    mutation(payload)

    with pytest.raises(ValidationError):
        SchemaManifest.model_validate(payload)


def test_schema_hash_is_stable_under_key_order_and_whitespace(
    manifest: SchemaManifest,
) -> None:
    reordered = json.loads(json.dumps(manifest.model_dump(mode="json"), sort_keys=True, indent=4))

    assert SchemaManifest.model_validate(reordered).schema_hash == manifest.schema_hash


def test_registration_is_idempotent_and_versions_are_immutable(
    tmp_path: Path,
    manifest: SchemaManifest,
) -> None:
    repository = SQLiteSchemaRepository(tmp_path / "registry.sqlite3")

    first = repository.register(manifest, "release@example.com")
    repeated = repository.register(manifest, "another@example.com")

    assert repeated == first
    assert len(repository.list("PRODUCTION", "invoice")) == 1

    changed = manifest.model_copy(update={"display_name": "Changed without a new version"})
    with pytest.raises(SchemaVersionConflictError, match="immutable"):
        repository.register(changed, "release@example.com")


def test_production_schema_list_filters_by_use_case(client: TestClient) -> None:
    response = client.get("/api/schemas?status=PRODUCTION&use_case=invoice")

    assert response.status_code == 200
    payload = response.json()
    # Every registered production version stays listed, newest first, so a prior version
    # remains selectable and inspectable.
    assert [(item["schema_id"], item["schema_version"]) for item in payload] == [
        ("invoice", 2),
        ("invoice", 1),
    ]
    assert payload[0]["display_name"] == "Invoice v2"
    assert all(len(item["schema_hash"]) == 64 for item in payload)
    assert payload[0]["schema_hash"] != payload[1]["schema_hash"]
    assert all(item["status"] == "PRODUCTION" for item in payload)
    assert client.get("/api/schemas?status=PRODUCTION&use_case=contract").json() == []


def test_schema_detail_exposes_fields_and_policies_without_raw_json(
    client: TestClient,
) -> None:
    response = client.get("/api/schemas/invoice/versions/1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["display_name"] == "Invoice v1"
    assert payload["fields"][0] == {
        "field_path": "invoice_number",
        "label": "Invoice Number",
        "field_type": "string",
        "description": "Invoice identifier exactly as stated by the seller.",
        "required": True,
        "citation_required": True,
        "confidence_threshold": 0.9,
        "risk_tier": "high",
    }
    assert payload["document_rules"][0]["tolerance"] == 0.01
    assert "ai_extract_schema" not in payload
    assert "field_policies" not in payload


def test_missing_unknown_and_untrusted_schema_requests_are_safe(
    client: TestClient,
) -> None:
    missing = client.get("/api/schemas/invoice/versions/99")
    non_production = client.get("/api/schemas?status=DRAFT&use_case=invoice")
    injected = client.post(
        "/api/schemas",
        json={"schema_id": "attacker", "ai_extract_schema": {"secret": {}}},
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SCHEMA_NOT_FOUND"
    assert non_production.status_code == 422
    assert injected.status_code == 405
    assert client.get("/api/schemas?status=PRODUCTION&use_case=attacker").json() == []
