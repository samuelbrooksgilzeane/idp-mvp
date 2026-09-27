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
