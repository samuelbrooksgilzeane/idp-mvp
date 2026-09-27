import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "smoke", Path(__file__).resolve().parents[2] / "scripts/workspace_smoke.py"
    )
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_smoke_is_three_metadata_gets_without_pagination() -> None:
    client = SimpleNamespace(config=SimpleNamespace(host="https://example.com"), api_client=Mock())
    client.api_client.do.return_value = {}
    result = module().check(client, "https://example.com", "abc")
    assert len(result) == 3 and all(item["ok"] for item in result)
    assert [call.args[0] for call in client.api_client.do.call_args_list] == ["GET"] * 3
    assert client.api_client.do.call_args_list[-1].kwargs["query"] == {"page_size": 5}


def test_smoke_stops_on_first_failure_and_does_not_print_error_details() -> None:
    client = SimpleNamespace(config=SimpleNamespace(host="https://example.com"), api_client=Mock())
    client.api_client.do.side_effect = ValueError("private configuration")
    result = module().check(client, "https://example.com", "abc")
    assert result == [{"check": "warehouse", "ok": False, "error_type": "ValueError"}]
    assert client.api_client.do.call_count == 1
    with pytest.raises(ValueError, match="Profile host"):
        module().check(client, "https://wrong.example.com", "abc")
    assert client.api_client.do.call_count == 1
