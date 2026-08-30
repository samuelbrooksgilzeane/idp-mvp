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
from idp_app.services.schemas import load_manifest, load_source_manifests, manifest_directory


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
    versions = [item["schema_version"] for item in payload]
    assert versions == sorted(versions, reverse=True)
    assert versions[-1] == 1 and len(versions) == len(set(versions)) > 1
    assert all(item["schema_id"] == "invoice" for item in payload)
    assert all(item["status"] == "PRODUCTION" for item in payload)
    assert all(len(item["schema_hash"]) == 64 for item in payload)
    assert len({item["schema_hash"] for item in payload}) == len(payload)
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
    # POST /api/schemas is the (new, generic) schema-creation endpoint; a client still cannot
    # choose its own schema_id or write raw ai_extract_schema JSON directly through it -- those
    # fields are not part of its request contract, so supplying them is a validation error.
    injected = client.post(
        "/api/schemas",
        json={"schema_id": "attacker", "ai_extract_schema": {"secret": {}}},
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SCHEMA_NOT_FOUND"
    assert non_production.status_code == 422
    assert injected.status_code == 422
    assert client.get("/api/schemas?status=PRODUCTION&use_case=attacker").json() == []


def test_create_schema_server_generates_its_own_schema_id(client: TestClient) -> None:
    """The schema_id is always derived server-side from the display name; the client never
    supplies it, matching the SQL-identifier-injection guardrail elsewhere in the app."""
    created = client.post(
        "/api/schemas",
        json={"display_name": "Custom Tax Form", "root_mode": "SINGLE_RECORD"},
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["schema_id"] not in ("attacker",)
    assert payload["status"] == "DRAFT"
    assert payload["root_mode"] == "SINGLE_RECORD"
    assert payload["is_editable"] is True

    # A second schema with a colliding display name gets its own distinct schema_id rather than
    # overwriting the first.
    again = client.post(
        "/api/schemas",
        json={"display_name": "Custom Tax Form", "root_mode": "SINGLE_RECORD"},
    )
    assert again.status_code == 201
    assert again.json()["schema_id"] != payload["schema_id"]


def test_schema_draft_edit_validate_and_publish_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/api/schemas",
        json={
            "display_name": "Nested Custom Schema",
            "root_mode": "REPEATED_RECORDS",
            "description": "A repeated-record schema for the editor tests.",
        },
    ).json()
    schema_id = created["schema_id"]

    nested_schema = {
        "records": {
            "type": "array",
            "description": "Each record.",
            "items": {
                "type": "object",
                "description": "One record.",
                "properties": {
                    "name": {"type": "string", "description": "Name."},
                    "amounts": {
                        "type": "array",
                        "description": "Line amounts.",
                        "items": {"type": "number", "description": "One amount."},
                    },
                },
            },
        }
    }

    validated = client.post(
        f"/api/schemas/{schema_id}/validate",
        json={"ai_extract_schema": nested_schema},
    )
    assert validated.status_code == 200
    assert validated.json()["valid"] is True
    assert validated.json()["leaf_count"] == 2

    updated = client.put(
        f"/api/schemas/{schema_id}/draft?schema_version=1",
        json={"ai_extract_schema": nested_schema},
    )
    assert updated.status_code == 200
    assert updated.json()["schema_tree"]["records"]["type"] == "array"

    published = client.post(f"/api/schemas/{schema_id}/publish?schema_version=1")
    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"
    assert published.json()["is_editable"] is False

    # A published version can no longer be edited directly...
    rejected = client.put(
        f"/api/schemas/{schema_id}/draft?schema_version=1",
        json={"ai_extract_schema": nested_schema},
    )
    assert rejected.status_code == 409

    # ...but cloning it (without a new schema_id) opens a new draft version to edit instead.
    cloned = client.post(
        f"/api/schemas/{schema_id}/clone?schema_version=1",
        json={"new_display_name": "Nested Custom Schema v2"},
    )
    assert cloned.status_code == 201
    assert cloned.json()["schema_id"] == schema_id
    assert cloned.json()["schema_version"] == 2
    assert cloned.json()["status"] == "DRAFT"

    listed = client.get("/api/schemas?status=ALL&use_case=generic").json()
    versions = {item["schema_version"] for item in listed if item["schema_id"] == schema_id}
    assert versions == {1, 2}


def test_schema_exceeding_limits_is_rejected_before_saving(client: TestClient) -> None:
    created = client.post(
        "/api/schemas",
        json={"display_name": "Oversized Schema", "root_mode": "SINGLE_RECORD"},
    ).json()
    schema_id = created["schema_id"]

    too_many_fields = {
        f"field_{index}": {"type": "string", "description": "Leaf."} for index in range(300)
    }
    validated = client.post(
        f"/api/schemas/{schema_id}/validate",
        json={"ai_extract_schema": too_many_fields},
    )
    assert validated.status_code == 200
    assert validated.json()["valid"] is False
    assert any("256" in error for error in validated.json()["errors"])

    saved = client.put(
        f"/api/schemas/{schema_id}/draft?schema_version=1",
        json={"ai_extract_schema": too_many_fields},
    )
    assert saved.status_code == 422


def test_every_hash_implementation_agrees_on_every_manifest() -> None:
    """The backend, the registration task and the extraction task each hash the manifest
    independently. If they disagree, a governed extraction fails its own integrity check, so
    the three implementations are pinned together here."""
    import hashlib
    import importlib.util
    import json
    import sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[2]
    modules = {}
    for name, relative in (
        ("register_schemas", "databricks_etl/src/register_schemas.py"),
        ("extract_document", "databricks_etl/src/extract_document.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules[name] = module

    manifests = load_source_manifests()
    assert len(manifests) >= 3
    for manifest in manifests:
        raw = json.loads(
            (root / "schemas" / f"invoice_v{manifest.schema_version}.json").read_text()
        )
        digests = {
            manifest.schema_hash,
            hashlib.sha256(
                modules["register_schemas"].canonical_json(raw).encode("utf-8")
            ).hexdigest(),
            hashlib.sha256(
                modules["extract_document"].canonical_json(raw).encode("utf-8")
            ).hexdigest(),
        }
        assert len(digests) == 1, f"hash implementations disagree for v{manifest.schema_version}"


def test_integral_numbers_hash_identically_however_they_are_written() -> None:
    """JSON does not distinguish 0 from 0.0, so neither may the canonical form."""
    manifest = load_source_manifests()[0]
    payload = manifest.model_dump(mode="json", exclude_none=True)
    integral = SchemaManifest.model_validate({**payload, "schema_version": 1})
    assert integral.canonical_json() == manifest.canonical_json()
