import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "prepare_genie_bundle",
        Path(__file__).resolve().parents[2] / "scripts/prepare_genie_bundle.py",
    )
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_native_overlay_connects_app_to_protected_empty_space() -> None:
    result = module().overlay(None, 7474660341420973)
    assert result["variables"]["genie_embed_url"]["default"] == (
        "${workspace.host}/embed/genie/rooms/"
        "${resources.genie_spaces.project_genie.id}?o=7474660341420973"
    )
    resource = result["resources"]["genie_spaces"]["project_genie"]
    assert result["bundle"]["engine"] == "direct"
    assert resource["lifecycle"] == {"prevent_destroy": True}
    assert json.loads(resource["serialized_space"])["data_sources"]["tables"] == []
    assert (
        result["variables"]["genie_space_id"]["default"]
        == "${resources.genie_spaces.project_genie.id}"
    )
    assert "permissions" not in resource


def test_export_is_preserved_exactly_and_update_is_etag_guarded() -> None:
    definition = '{"version":2,"instructions":{"human_edit":true},"unknown_future_field":42}'
    remote = SimpleNamespace(
        serialized_space=definition,
        etag="current",
        title="Human title",
        description="Human description",
        warehouse_id="warehouse",
    )
    result = module().overlay(remote, 123)["resources"]["genie_spaces"]["project_genie"]
    assert result["serialized_space"] == definition
    assert "etag" not in result  # Native CLI rejects explicitly configured ETags.
    assert result["title"] == "Human title"
    remote.etag = None
    with pytest.raises(ValueError, match="ETag"):
        module().overlay(remote, 123)


def test_project_views_are_added_once_and_curation_is_kept() -> None:
    exported = json.dumps({
        "version": 2,
        "instructions": {"human_edit": True},
        "data_sources": {"tables": [
            {"identifier": "other.curated.table", "description": ["kept"]},
            {"identifier": "WORKSPACE.idp_mvp.idp_dev_genie_fields", "description": ["human text"]},
        ]},
    })
    remote = SimpleNamespace(serialized_space=exported, etag="e", title=None, description=None,
                             warehouse_id=None)
    views = ("workspace", "idp_mvp", "idp_dev")
    space = json.loads(module().overlay(remote, 1, views)["resources"]["genie_spaces"]
                       ["project_genie"]["serialized_space"])
    identifiers = [table["identifier"] for table in space["data_sources"]["tables"]]
    assert identifiers == sorted(identifiers, key=str.lower)
    assert "other.curated.table" in identifiers
    assert [i.lower() for i in identifiers].count("workspace.idp_mvp.idp_dev_genie_fields") == 1
    for view in ("documents", "extractions", "records"):
        assert f"workspace.idp_mvp.idp_dev_genie_{view}" in identifiers
    assert space["instructions"] == {"human_edit": True}
    again = module().with_project_views(json.dumps(space), *views)
    assert json.loads(again) == space


def test_new_space_gets_project_views_and_identifiers_are_validated() -> None:
    space = json.loads(module().overlay(None, 1, ("cat", "sch", "idp"))["resources"]
                       ["genie_spaces"]["project_genie"]["serialized_space"])
    assert len(space["data_sources"]["tables"]) == 4
    with pytest.raises(ValueError, match="identifier"):
        module().with_project_views("{}", "cat", "sch; DROP", "idp")
