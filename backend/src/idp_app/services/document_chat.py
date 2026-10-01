"""Document chat: ask the chat Supervisor about IDP documents, with per-user history.

The Supervisor serving endpoint (scripts/provision_chat.py) answers from the chat UC functions and,
where it works, the Knowledge Assistant. Answers and citations are plain text. Source PDFs are
stored as <document_id>.pdf, so document ids are rewritten to original file names before an answer
is stored or shown. History is kept per user: every read and write is scoped to the caller.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.sql_retry import run_with_retries

logger = logging.getLogger(__name__)
DOCUMENT_FILE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(\.pdf)?\b"
)
TRACE_ID = re.compile(r"^tr-[0-9a-f]{32}$")
AGENT_NAME = re.compile(r"^\s*<name>.*</name>\s*$", re.DOTALL)
# Older turns sent back to the endpoint; enough context, bounded request size.
HISTORY_TURNS = 12
MAX_CONVERSATIONS = 50


class ChatError(DocumentServiceError):
    """Rendered by the app's DocumentServiceError handler as {"error": {code, message}}."""


@dataclass(frozen=True)
class Answer:
    text: str
    tools: list[str]
    citations: list[str]
    trace_id: str | None = None


def parse_response(response: dict[str, Any]) -> Answer:
    """Read a Responses-API reply: the last real message is the answer.

    Earlier messages are progress notes ("I'll query ...") and <name> tags naming sub-agents."""
    messages: list[str] = []
    tools: list[str] = []
    citations: list[str] = []
    for item in response.get("output") or []:
        if item.get("type") == "function_call" and item.get("name"):
            name = str(item["name"]).rsplit("__", 1)[-1]
            if name not in tools:
                tools.append(name)
        if item.get("type") != "message":
            continue
        text = "".join(
            str(part.get("text") or "")
            for part in item.get("content") or []
            if part.get("type") in ("output_text", "text")
        )
        for part in item.get("content") or []:
            for annotation in part.get("annotations") or []:
                label = str(annotation.get("title") or annotation.get("url") or "").strip()
                if label and label not in citations:
                    citations.append(label)
        if text.strip() and not AGENT_NAME.match(text):
            messages.append(text.strip())
    if not messages:
        raise ChatError("CHAT_EMPTY_ANSWER", "The assistant returned no answer.", 502)
    return Answer(messages[-1], tools, citations, trace_id(response))


def trace_id(response: dict[str, Any]) -> str | None:
    """The MLflow trace id the endpoint returns when asked with return_trace (else None)."""
    trace = (response.get("databricks_output") or {}).get("trace") or {}
    info = trace.get("info") or {}
    value = info.get("trace_id") or info.get("request_id")
    return value if isinstance(value, str) and TRACE_ID.fullmatch(value) else None


def rewrite_document_names(text: str, names: dict[str, str]) -> str:
    """Replace <document_id>.pdf (or a bare document id) with the document's original file name."""

    def replace(match: re.Match[str]) -> str:
        return names.get(match.group(1), match.group(0))

    return DOCUMENT_FILE.sub(replace, text)


def document_ids(*texts: str) -> set[str]:
    return {match.group(1) for text in texts for match in DOCUMENT_FILE.finditer(text)}


class ChatClient(Protocol):
    def ask(self, messages: list[dict[str, str]]) -> dict[str, Any]: ...

    def tag_trace(self, trace_id: str, tags: dict[str, str]) -> None:
        """Label the endpoint's MLflow trace so operators can search it by user/conversation."""
        ...


class ServingEndpointChatClient:
    def __init__(self, client: Any, endpoint: str) -> None:
        self.api = client.api_client
        self.path = f"/serving-endpoints/{endpoint}/invocations"

    def ask(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            # return_trace adds the MLflow trace (id, spans) under databricks_output.trace.
            response: dict[str, Any] = self.api.do(
                "POST",
                self.path,
                body={"input": messages, "databricks_options": {"return_trace": True}},
            )
        except Exception as error:
            # No raw text: endpoint errors can echo request content or configuration.
            logger.warning(
                "Chat endpoint call failed: %s %s",
                type(error).__name__,
                getattr(error, "error_code", None) or "",
            )
            raise ChatError(
                "CHAT_ENDPOINT_FAILED", "The document assistant could not answer. Try again.", 502
            ) from error
        return response

    def tag_trace(self, trace_id: str, tags: dict[str, str]) -> None:
        path = f"/api/2.0/mlflow/traces/{trace_id}/tags"
        for key, value in tags.items():
            self.api.do("PATCH", path, body={"key": key, "value": value})


class MockChatClient:
    """Local development: a canned answer that exercises tables, tools and citations."""

    def tag_trace(self, trace_id: str, tags: dict[str, str]) -> None:
        return None

    def ask(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        question = messages[-1]["content"]
        answer = (
            f"Mock answer to: {question}\n\n"
            "| Document | Field | Value |\n|---|---|---|\n"
            "| invoice.pdf | total | 540.00 |\n| invoice.pdf | seller_name | Northwind |"
        )
        return {
            "output": [
                {"type": "function_call", "name": "workspace__idp_mvp__idp_dev_chat_invoices"},
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": answer,
                            "annotations": [{"title": "invoice.pdf"}],
                        }
                    ],
                },
            ]
        }


class ChatRepository(Protocol):
    def conversations(self, user: str) -> list[dict[str, Any]]: ...
    def messages(self, user: str, conversation_id: str) -> list[dict[str, Any]]: ...
    def append(self, user: str, conversation_id: str, messages: list[dict[str, Any]]) -> None: ...


def _summaries(rows: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """Rows of (conversation_id, first user message, last created_at), newest first."""
    return [
        {"conversation_id": cid, "title": title[:80], "updated_at": updated}
        for cid, title, updated in rows
    ]


class SQLiteChatRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS chat_messages (conversation_id TEXT NOT NULL, "
                "user_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL, "
                "payload TEXT NOT NULL, created_at TEXT NOT NULL, "
                "PRIMARY KEY (conversation_id, seq))"
            )

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def conversations(self, user: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT conversation_id, "
                "MAX(CASE WHEN seq = 0 THEN json_extract(payload, '$.text') END), "
                "MAX(created_at) FROM chat_messages WHERE user_id = ? "
                "GROUP BY conversation_id ORDER BY MAX(created_at) DESC LIMIT ?",
                (user, MAX_CONVERSATIONS),
            ).fetchall()
        return _summaries(rows)

    def messages(self, user: str, conversation_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM chat_messages WHERE user_id = ? AND conversation_id = ? "
                "ORDER BY seq",
                (user, conversation_id),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append(self, user: str, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO chat_messages VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        conversation_id,
                        user,
                        message["seq"],
                        message["role"],
                        json.dumps(message),
                        message["created_at"],
                    )
                    for message in messages
                ],
            )


class DatabricksChatRepository:
    def __init__(self, sql: DatabricksDocumentRegistry, namespace: str) -> None:
        self.sql = sql
        self.table = f"{namespace}_chat_messages"

    def conversations(self, user: str) -> list[dict[str, Any]]:
        rows = self.sql.execute_sql(
            "SELECT conversation_id, "
            "max(CASE WHEN seq = 0 THEN get_json_object(payload, '$.text') END), "
            f"CAST(max(created_at) AS STRING) FROM {self.table} WHERE user_id = :user "
            "GROUP BY conversation_id ORDER BY max(created_at) DESC "
            "LIMIT CAST(:limit AS INT)",
            {"user": user, "limit": MAX_CONVERSATIONS},
        )
        return _summaries([(row[0], row[1] or "", row[2]) for row in rows])

    def messages(self, user: str, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.sql.execute_sql(
            f"SELECT payload FROM {self.table} WHERE user_id = :user "
            "AND conversation_id = :conversation_id ORDER BY seq",
            {"user": user, "conversation_id": conversation_id},
        )
        return [json.loads(row[0]) for row in rows]

    def append(self, user: str, conversation_id: str, messages: list[dict[str, Any]]) -> None:
        # The caller passes each message's seq; MERGE on (conversation_id, seq) makes retries safe.
        rows = [
            {
                "conversation_id": conversation_id,
                "user_id": user,
                "seq": message["seq"],
                "role": message["role"],
                "payload": json.dumps(message),
                "created_at": message["created_at"],
            }
            for message in messages
        ]
        run_with_retries(
            lambda: self.sql.execute_dml(
                f"MERGE INTO {self.table} t USING (SELECT m.conversation_id, m.user_id, m.seq, "
                "m.role, m.payload, CAST(m.created_at AS TIMESTAMP) AS created_at FROM "
                "(SELECT explode(from_json(:rows, 'array<struct<conversation_id:string,"
                "user_id:string,seq:int,role:string,payload:string,created_at:string>>')) m)) s "
                "ON t.conversation_id = s.conversation_id AND t.seq = s.seq "
                "WHEN NOT MATCHED THEN INSERT *",
                {"rows": json.dumps(rows)},
            ),
            None,
            label="chat history write",
        )


class DocumentChatService:
    def __init__(
        self,
        client: ChatClient,
        repository: ChatRepository,
        file_names: Callable[[list[str]], dict[str, str]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.client = client
        self.repository = repository
        self.file_names = file_names
        self.clock = clock
        self._names: dict[str, str] = {}

    def conversations(self, user: str) -> list[dict[str, Any]]:
        return self.repository.conversations(user)

    def conversation(self, user: str, conversation_id: str) -> list[dict[str, Any]]:
        messages = self.repository.messages(user, _conversation_id(conversation_id))
        if not messages:
            raise ChatError("CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
        return [_public(message) for message in messages]

    def ask(self, user: str, conversation_id: str | None, text: str) -> dict[str, Any]:
        question = text.strip()
        if not question:
            raise ChatError("EMPTY_QUESTION", "Type a question.", 422)
        if conversation_id is None:
            conversation_id, history = str(uuid.uuid4()), []
        else:
            history = self.conversation(user, conversation_id)
        context = [
            {"role": message["role"], "content": message["text"]}
            for message in history[-HISTORY_TURNS:]
        ]
        started = time.monotonic()
        answer = parse_response(self.client.ask([*context, {"role": "user", "content": question}]))
        names = self._lookup(document_ids(answer.text, *answer.citations))
        now = self.clock().isoformat()
        asked = {"seq": len(history), "role": "user", "text": question, "created_at": now}
        reply = {
            "seq": len(history) + 1,
            "role": "assistant",
            "text": rewrite_document_names(answer.text, names),
            "tools": answer.tools,
            "citations": list(
                dict.fromkeys(rewrite_document_names(c, names) for c in answer.citations)
            ),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "created_at": now,
        }
        if answer.trace_id:
            # Stored for operators (history row -> MLflow trace); never sent to the browser.
            reply["trace_id"] = answer.trace_id
            self._tag(answer.trace_id, user, conversation_id)
        self.repository.append(user, conversation_id, [asked, reply])
        return {"conversation_id": conversation_id, "messages": [asked, _public(reply)]}

    def _tag(self, trace: str, user: str, conversation_id: str) -> None:
        try:
            self.client.tag_trace(
                trace, {"idp.user": user, "idp.conversation_id": conversation_id}
            )
        except Exception as error:
            # Best effort: the trace id is already stored with the answer.
            logger.warning(
                "Chat trace tagging failed: %s %s",
                type(error).__name__,
                getattr(error, "error_code", None) or "",
            )

    def _lookup(self, ids: set[str]) -> dict[str, str]:
        missing = sorted(ids - self._names.keys())[:20]
        if missing:
            # One query, not one per document. On failure the ids stay as they are; the answer
            # is still shown.
            with contextlib.suppress(Exception):
                self._names.update(self.file_names(missing))
        return {key: self._names[key] for key in ids if key in self._names}


def _conversation_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise ChatError("CONVERSATION_NOT_FOUND", "Conversation not found.", 404) from error


def _public(message: dict[str, Any]) -> dict[str, Any]:
    """A history message as the browser sees it: operator-only fields removed."""
    return {key: value for key, value in message.items() if key != "trace_id"}
