"""Chat provisioning against an in-memory fake of the Agent Bricks REST API."""

import importlib.util
import itertools
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "provision_chat", Path(__file__).resolve().parents[2] / "scripts/provision_chat.py"
    )
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(result)
    return result


class FakeAgentBricks:
    """Collections keyed by REST path; records every write."""

    def __init__(self) -> None:
        self.items: dict[str, list[dict[str, Any]]] = {}
        self.writes: list[tuple[str, str]] = []
        self.ids = (f"id{n}" for n in itertools.count(1))

    def do(self, method: str, path: str, query=None, body=None):
        collection = path.removeprefix("/api/2.1/")
        if method == "GET":
            key = collection.rsplit("/", 1)[-1].replace("-", "_")
            return {key: list(self.items.get(collection, []))}
        self.writes.append((method, collection))
        if method == "POST":
            item_id = (query or {}).get("tool_id") or next(self.ids)
            item = {**body, "name": f"{collection}/{item_id}", "id": item_id}
            if collection == "knowledge-assistants":
                item["endpoint_name"] = f"ka-{item_id}-endpoint"
            if collection == "supervisor-agents":
                item["endpoint_name"] = f"mas-{item_id}-endpoint"
            if collection.endswith("/tools"):
                item["tool_id"] = item_id
            self.items.setdefault(collection, []).append(item)
            return item
        if method == "PATCH":
            for items in self.items.values():
                for item in items:
                    if item["name"] == collection:
                        for key in query["update_mask"].split(","):
                            item[key] = body[key]
                        return item
        raise AssertionError((method, path))


def settings(provision, **overrides):
    values = {
        "app_name": "idp-mvp",
        "target": "dev",
        "catalog": "workspace",
        "project_schema": "idp_mvp",
        "table_prefix": "idp_dev",
        "source_path": "/Volumes/workspace/idp_mvp/idp_source/incoming/",
    }
    return provision.Settings(**{**values, **overrides})


def run(provision, api, dry_run=False, **overrides):
    client = SimpleNamespace(api_client=api)
    return provision.Provisioner(client, settings(provision, **overrides), dry_run).run()


def test_creates_ka_source_supervisor_and_tools_then_rerun_is_a_no_op() -> None:
    provision = module()
    api = FakeAgentBricks()

    report = run(provision, api)

    assert report.endpoints == {
        "knowledge_assistant": "ka-id1-endpoint",
        "supervisor": "mas-id3-endpoint",
    }
    ka = api.items["knowledge-assistants"][0]
    assert ka["display_name"] == "idp-mvp-dev-documents-ka"
    source = api.items["knowledge-assistants/id1/knowledge-sources"][0]
    assert source["source_type"] == "files"
    assert source["files"] == {"path": "/Volumes/workspace/idp_mvp/idp_source/incoming/"}
    supervisor = api.items["supervisor-agents"][0]
    assert supervisor["display_name"] == "idp-mvp-dev-chat-supervisor"
    tools = {t["tool_id"]: t for t in api.items["supervisor-agents/id3/tools"]}
    assert tools["documents"]["knowledge_assistant"] == {"knowledge_assistant_id": "id1"}
    assert tools["invoice_totals"]["uc_function"] == {
        "name": "workspace.idp_mvp.idp_dev_chat_invoice_totals"
    }
    assert len(tools) == 6

    writes = len(api.writes)
    again = run(provision, api)
    assert len(api.writes) == writes
    assert again.actions == [] and again.warnings == []
    assert again.endpoints == report.endpoints


def test_drifted_instructions_and_tool_descriptions_are_patched_in_place() -> None:
    provision = module()
    api = FakeAgentBricks()
    run(provision, api)
    api.items["supervisor-agents"][0]["instructions"] = "old"
    api.items["supervisor-agents/id3/tools"][1]["description"] = "old"
    api.writes.clear()

    report = run(provision, api)

    assert api.writes == [
        ("PATCH", "supervisor-agents/id3"),
        ("PATCH", "supervisor-agents/id3/tools/document_fields"),
    ]
    assert api.items["supervisor-agents"][0]["instructions"] == provision.SUPERVISOR_INSTRUCTIONS
    assert report.actions == [
        "update supervisor agent idp-mvp-dev-chat-supervisor: instructions",
        "update tool document_fields description",
    ]


def test_dry_run_only_reads() -> None:
    provision = module()
    api = FakeAgentBricks()

    report = run(provision, api, dry_run=True)

    assert api.writes == []
    assert report.actions[0] == "create knowledge assistant idp-mvp-dev-documents-ka"
    assert "create tool documents" in report.actions
    assert report.endpoints == {"knowledge_assistant": None, "supervisor": None}


def test_without_knowledge_assistant_the_supervisor_gets_functions_only() -> None:
    provision = module()
    api = FakeAgentBricks()

    run(provision, api, with_knowledge_assistant=False)

    assert "knowledge-assistants" not in api.items
    tools = api.items["supervisor-agents/id1/tools"]
    assert {t["tool_type"] for t in tools} == {"uc_function"}


def test_foreign_source_and_tools_are_reported_never_changed() -> None:
    provision = module()
    api = FakeAgentBricks()
    run(provision, api)
    api.items["knowledge-assistants/id1/knowledge-sources"][0]["files"]["path"] = "/Volumes/x/y/z/"
    api.items["supervisor-agents/id3/tools"].append({"tool_id": "extra", "name": "n"})
    api.items["supervisor-agents/id3/tools"][0]["knowledge_assistant"] = {
        "knowledge_assistant_id": "other"
    }
    api.writes.clear()

    report = run(provision, api)

    assert api.writes == []
    assert len(report.warnings) == 3


def test_duplicate_display_names_stop_the_run() -> None:
    provision = module()
    api = FakeAgentBricks()
    api.items["supervisor-agents"] = [
        {"display_name": "idp-mvp-dev-chat-supervisor", "name": "supervisor-agents/a"},
        {"display_name": "idp-mvp-dev-chat-supervisor", "name": "supervisor-agents/b"},
    ]
    with pytest.raises(ValueError, match="More than one"):
        run(provision, api, with_knowledge_assistant=False)


@pytest.mark.parametrize(
    "overrides",
    [
        {"target": "Dev"},
        {"table_prefix": "idp-dev"},
        {"source_path": "/Volumes/workspace/idp_mvp/idp_source/../other/"},
        {"source_path": "/tmp/incoming/"},
    ],
)
def test_rejects_unsafe_settings(overrides) -> None:
    provision = module()
    with pytest.raises(ValueError):
        provision.validate_settings(settings(provision, **overrides))
