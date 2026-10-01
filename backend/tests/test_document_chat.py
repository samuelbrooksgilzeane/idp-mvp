"""Document chat: response parsing, document name rewriting, per-user history and the API."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from idp_app.core.config import IdpMode, Settings
from idp_app.main import create_app
from idp_app.services.document_chat import (
    ChatError,
    DocumentChatService,
    SQLiteChatRepository,
    parse_response,
    rewrite_document_names,
)

DOC = "d964074f-52b7-5c2e-9fd7-3cfcec27bc08"
TRACE = "tr-10764b64efd67c5236438c723a6bcd05"

# Shape of a real Supervisor reply (probe, 1 October 2026): progress notes, agent name tags, tool
# calls named <catalog>__<schema>__<function>, then the answer.
SUPERVISOR_REPLY: dict[str, Any] = {
    "output": [
        {"type": "message", "content": [{"type": "output_text", "text": "I'll look that up."}]},
        {"type": "function_call", "name": "workspace__idp_mvp__idp_dev_chat_document_fields"},
        {"type": "function_call_output", "output": "[...]"},
        {"type": "message", "content": [{"type": "output_text", "text": "<name>idp-ka</name>"}]},
        {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": f"| Document | Field | Value |\n|---|---|---|\n| {DOC}.pdf | t | 1 |",
                    "annotations": [{"title": f"{DOC}.pdf", "url": "https://x/y.pdf"}],
                }
            ],
        },
    ],
    "databricks_output": {"trace": {"info": {"trace_id": TRACE}, "data": {"spans": []}}},
}


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[list[dict[str, str]]] = []
        self.tags: list[tuple[str, dict[str, str]]] = []
        self.tagging_fails = False

    def tag_trace(self, trace_id: str, tags: dict[str, str]) -> None:
        if self.tagging_fails:
            raise RuntimeError("PERMISSION_DENIED")
        self.tags.append((trace_id, tags))

    def ask(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        self.requests.append(messages)
        return SUPERVISOR_REPLY


def service(tmp_path: Path) -> tuple[DocumentChatService, FakeClient, list[str]]:
    client = FakeClient()
    lookups: list[str] = []

    def file_names(document_ids: list[str]) -> dict[str, str]:
        lookups.extend(document_ids)
        return {DOC: "50080tihd.pdf"} if DOC in document_ids else {}

    repository = SQLiteChatRepository(tmp_path / "chat.sqlite3")
    return DocumentChatService(client, repository, file_names), client, lookups


def test_parse_keeps_the_final_answer_tools_and_citations() -> None:
    answer = parse_response(SUPERVISOR_REPLY)
    assert answer.text.startswith("| Document |")
    assert answer.tools == ["idp_dev_chat_document_fields"]
    assert answer.citations == [f"{DOC}.pdf"]


def test_parse_rejects_a_reply_without_an_answer() -> None:
    with pytest.raises(ChatError):
        parse_response({"output": [{"type": "message", "content": [{"text": "<name>a</name>"}]}]})


def test_rewrite_replaces_known_ids_and_leaves_others() -> None:
    other = "11111111-2222-3333-4444-555555555555"
    text = f"See {DOC}.pdf, {DOC} and {other}.pdf."
    assert rewrite_document_names(text, {DOC: "a.pdf"}) == f"See a.pdf, a.pdf and {other}.pdf."


def test_ask_stores_rewritten_answer_and_continues_with_context(tmp_path: Path) -> None:
    chat, client, lookups = service(tmp_path)

    first = chat.ask("ann@example.com", None, "What is the total?")
    conversation_id = first["conversation_id"]
    reply = first["messages"][1]
    assert "| 50080tihd.pdf | t | 1 |" in reply["text"]
    assert reply["citations"] == ["50080tihd.pdf"]
    assert reply["tools"] == ["idp_dev_chat_document_fields"]

    chat.ask("ann@example.com", conversation_id, "And the seller?")
    assert client.requests[1][:2] == [
        {"role": "user", "content": "What is the total?"},
        {"role": "assistant", "content": reply["text"]},
    ]
    assert lookups == [DOC]  # cached after the first lookup
    messages = chat.conversation("ann@example.com", conversation_id)
    assert [m["seq"] for m in messages] == [0, 1, 2, 3]
    assert chat.conversations("ann@example.com")[0]["title"] == "What is the total?"


def test_history_is_private_to_each_user(tmp_path: Path) -> None:
    chat, _, _ = service(tmp_path)
    conversation_id = chat.ask("ann@example.com", None, "Q")["conversation_id"]

    assert chat.conversations("bob@example.com") == []
    with pytest.raises(ChatError) as error:
        chat.conversation("bob@example.com", conversation_id)
    assert error.value.status_code == 404
    with pytest.raises(ChatError):
        chat.ask("bob@example.com", conversation_id, "Q")  # cannot append to Ann's chat
    with pytest.raises(ChatError):
        chat.conversation("ann@example.com", "not-a-uuid")


def test_api_round_trip_in_mock_mode(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, local_data_dir=tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/app-config").json()["chat_enabled"] is True
        asked = client.post("/api/chat/messages", json={"text": "Totals by supplier?"})
        assert asked.status_code == 200, asked.text
        body = asked.json()
        assert body["messages"][1]["tools"] == ["idp_dev_chat_invoices"]
        listed = client.get("/api/chat/conversations").json()["conversations"]
        assert [c["conversation_id"] for c in listed] == [body["conversation_id"]]
        fetched = client.get(f"/api/chat/conversations/{body['conversation_id']}")
        assert len(fetched.json()["messages"]) == 2
        assert client.post("/api/chat/messages", json={"text": ""}).status_code == 422
        missing = client.get("/api/chat/conversations/00000000-0000-0000-0000-000000000000")
        assert missing.status_code == 404


def test_chat_is_off_in_databricks_mode_without_an_endpoint() -> None:
    settings = Settings(
        _env_file=None,
        mode=IdpMode.DATABRICKS,
        catalog="workspace",
        project_schema="idp_mvp",
        table_prefix="idp_dev",
        source_volume_name="idp_source",
        artifacts_volume_name="idp_artifacts",
        warehouse_id="abc",
        parse_job_id=1,
        extraction_job_id=1,
        chat_endpoint=" ",
    )
    assert settings.chat_endpoint is None and settings.chat_enabled is False
    assert settings.model_copy(update={"chat_endpoint": "mas-1-endpoint"}).chat_enabled


def test_trace_id_is_stored_and_tagged_but_never_returned(tmp_path: Path) -> None:
    chat, client, _ = service(tmp_path)

    asked = chat.ask("ann@example.com", None, "Q")
    conversation_id = asked["conversation_id"]

    assert parse_response(SUPERVISOR_REPLY).trace_id == TRACE
    assert all("trace_id" not in message for message in asked["messages"])
    assert all("trace_id" not in m for m in chat.conversation("ann@example.com", conversation_id))
    stored = chat.repository.messages("ann@example.com", conversation_id)
    assert stored[1]["trace_id"] == TRACE
    assert client.tags == [
        (TRACE, {"idp.user": "ann@example.com", "idp.conversation_id": conversation_id})
    ]


def test_tagging_failure_or_missing_trace_never_blocks_the_answer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    chat, client, _ = service(tmp_path)
    client.tagging_fails = True
    assert chat.ask("ann@example.com", None, "Q")["messages"][1]["role"] == "assistant"
    assert "trace tagging failed" in caplog.text

    untraced = {key: value for key, value in SUPERVISOR_REPLY.items() if key != "databricks_output"}
    assert parse_response(untraced).trace_id is None
    bad = {**SUPERVISOR_REPLY, "databricks_output": {"trace": {"info": {"trace_id": "x/../y"}}}}
    assert parse_response(bad).trace_id is None


def test_endpoint_client_requests_the_trace_and_tags_it() -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from idp_app.services.document_chat import ServingEndpointChatClient

    api = Mock()
    api.do.return_value = SUPERVISOR_REPLY
    client = ServingEndpointChatClient(SimpleNamespace(api_client=api), "mas-1-endpoint")

    client.ask([{"role": "user", "content": "Q"}])
    method, path = api.do.call_args.args
    assert (method, path) == ("POST", "/serving-endpoints/mas-1-endpoint/invocations")
    assert api.do.call_args.kwargs["body"]["databricks_options"] == {"return_trace": True}

    client.tag_trace(TRACE, {"idp.user": "ann@example.com"})
    assert api.do.call_args.args == ("PATCH", f"/api/2.0/mlflow/traces/{TRACE}/tags")
    assert api.do.call_args.kwargs == {"body": {"key": "idp.user", "value": "ann@example.com"}}


def test_concurrent_turns_on_one_conversation_are_both_kept(tmp_path: Path) -> None:
    chat, client, _ = service(tmp_path)
    conversation_id = chat.ask("ann@example.com", None, "First")["conversation_id"]
    ask = client.ask

    def second_tab_answers_first(messages: list[dict[str, str]]) -> dict[str, Any]:
        client.ask = ask  # the second tab's own call answers normally
        chat.ask("ann@example.com", conversation_id, "Second tab")
        return ask(messages)

    client.ask = second_tab_answers_first  # type: ignore[method-assign]
    slow = chat.ask("ann@example.com", conversation_id, "First tab")

    assert [m["seq"] for m in slow["messages"]] == [4, 5]
    assert all("turn_id" not in m for m in slow["messages"])
    stored = chat.conversation("ann@example.com", conversation_id)
    assert [m["text"] for m in stored if m["role"] == "user"] == [
        "First",
        "Second tab",
        "First tab",
    ]
    assert [m["seq"] for m in stored] == [0, 1, 2, 3, 4, 5]


class LostAnswerRepository(SQLiteChatRepository):
    """The write commits but its row count is lost, as after a retried warehouse MERGE."""

    def append(self, user: str, conversation_id: str, messages: list[dict[str, Any]]) -> int:
        super().append(user, conversation_id, messages)
        return 0


class AlwaysTakenRepository(SQLiteChatRepository):
    def append(self, user: str, conversation_id: str, messages: list[dict[str, Any]]) -> int:
        return 0


def test_committed_turn_with_a_lost_answer_is_not_stored_twice(tmp_path: Path) -> None:
    chat = DocumentChatService(FakeClient(), LostAnswerRepository(tmp_path / "c.db"), dict)
    conversation_id = chat.ask("ann@example.com", None, "Q")["conversation_id"]
    assert len(chat.conversation("ann@example.com", conversation_id)) == 2


def test_turn_that_keeps_losing_its_place_reports_a_conflict(tmp_path: Path) -> None:
    chat = DocumentChatService(FakeClient(), AlwaysTakenRepository(tmp_path / "c.db"), dict)
    with pytest.raises(ChatError) as error:
        chat.ask("ann@example.com", None, "Q")
    assert error.value.status_code == 409
