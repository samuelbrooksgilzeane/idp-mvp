"""Document chat: ask the chat endpoint about IDP documents, with per-user history.

The endpoint is the Knowledge Assistant, or in supervisor mode a Supervisor over the KA and the chat
UC functions, as provisioned by the workspace_setup Job (or the chat_endpoint override). Answers are
plain text. Knowledge Assistant citations that point at a source PDF become numbered sources the
browser opens at the cited page; any other citation stays a plain label. Source PDFs are
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from idp_app.services.document_registry import DatabricksDocumentRegistry
from idp_app.services.documents import DocumentServiceError
from idp_app.services.sql_retry import run_with_retries

logger = logging.getLogger(__name__)
DOCUMENT_FILE = re.compile(
    r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})(\.pdf)?\b"
)
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
TRACE_ID = re.compile(r"^tr-[0-9a-f]{32}$")
# A cited URL's fragment: #page=N and the passage as a text directive (text=... or :~:text=...).
PAGE_FRAGMENT = re.compile(r"(?:^|[&#;])page=(\d{1,5})(?=$|[&#;:])")
TEXT_FRAGMENT = re.compile(r"(?::~:|[&#;]|^)text=([^&#]*)")
MAX_QUOTES = 5
MAX_QUOTE_CHARS = 500
ENDPOINT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
AGENT_NAME = re.compile(r"^\s*<name>.*</name>\s*$", re.DOTALL)
# Older turns sent back to the endpoint; enough context, bounded request size.
HISTORY_TURNS = 12
MAX_CONVERSATIONS = 50


class ChatError(DocumentServiceError):
    """Rendered by the app's DocumentServiceError handler as {"error": {code, message}}."""


@dataclass(frozen=True)
class SourceLocation:
    """Where a citation must point to become a link: the workspace host and the source volume's
    incoming/ folder, where the app stores each PDF as <document_id>.pdf."""

    host: str
    catalog: str
    project_schema: str
    volume: str

    def document_id(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme != "https" or (parts.hostname or "").lower() != self.host.lower():
            return None
        path = unquote(parts.path)
        names = (self.catalog, self.project_schema, self.volume)
        folder = "/".join(re.escape(name) for name in names)
        match = re.search(rf"/{folder}/incoming/({UUID})\.pdf$", path)
        return match.group(1) if match and ".." not in path.split("/") else None


@dataclass
class Citation:
    """One numbered source of an answer. Citations of the same document page share a number;
    ``parts`` are the indexes of the answer's text parts that carried it."""

    number: int
    label: str
    document_id: str | None = None
    page: int | None = None
    quotes: list[str] = field(default_factory=list)
    parts: list[int] = field(default_factory=list)

    def public(self, names: dict[str, str]) -> dict[str, Any]:
        named = names.get(self.document_id) if self.document_id else None
        return {
            "number": self.number,
            "file_name": named or rewrite_document_names(self.label, names),
            "document_id": self.document_id,
            "page": self.page,
            "quotes": self.quotes,
            "parts": self.parts,
        }


@dataclass(frozen=True)
class Answer:
    text: str
    tools: list[str]
    citations: list[Citation]
    trace_id: str | None = None
    # The answer message's text parts, in order; citations name them by index.
    parts: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Message:
    text: str
    parts: list[str]
    annotations: list[list[dict[str, Any]]]


def parse_response(response: dict[str, Any], sources: SourceLocation | None = None) -> Answer:
    """Read a Responses-API reply: the last real message is the answer.

    Earlier messages are progress notes ("I'll query ...") and <name> tags naming sub-agents.
    The answer's citations are numbered first, in the order of its text parts."""
    messages: list[_Message] = []
    tools: list[str] = []
    for item in response.get("output") or []:
        if item.get("type") == "function_call" and item.get("name"):
            name = str(item["name"]).rsplit("__", 1)[-1]
            if name not in tools:
                tools.append(name)
        if item.get("type") != "message":
            continue
        content = [part for part in item.get("content") or [] if isinstance(part, dict)]
        texts = [part for part in content if part.get("type") in ("output_text", "text")]
        parts = [str(part.get("text") or "") for part in texts]
        annotations = [list(part.get("annotations") or []) for part in texts]
        annotations += [
            list(part.get("annotations") or []) for part in content if part not in texts
        ]
        messages.append(_Message("".join(parts), parts, annotations))
    answers = [m for m in messages if m.text.strip() and not AGENT_NAME.match(m.text)]
    if not answers:
        raise ChatError("CHAT_EMPTY_ANSWER", "The assistant returned no answer.", 502)
    final = answers[-1]
    citations = _Citations(sources)
    for index, cited in enumerate(final.annotations):
        for annotation in cited:
            citations.add(annotation, index if index < len(final.parts) else None)
    for message in messages:
        if message is not final:
            for cited in message.annotations:
                for annotation in cited:
                    citations.add(annotation, None)
    return Answer(final.text.strip(), tools, citations.items, trace_id(response), final.parts)


class _Citations:
    def __init__(self, sources: SourceLocation | None) -> None:
        self.sources = sources
        self.items: list[Citation] = []
        self.by_key: dict[tuple[str | None, object], Citation] = {}

    def add(self, annotation: Any, part: int | None) -> None:
        if not isinstance(annotation, dict):
            return
        url = str(annotation.get("url") or "")
        label = str(annotation.get("title") or url).strip()
        document_id = self.sources.document_id(url) if self.sources and url else None
        if url and document_id is None and annotation.get("type") == "url_citation":
            logger.info("Citation kept as a label: %s", urlsplit(url).hostname or "no host")
        page, quote = citation_fragment(url) if document_id else (None, None)
        if not label and document_id is None:
            return
        key: tuple[str | None, object] = (document_id, page) if document_id else (None, label)
        citation = self.by_key.get(key)
        if citation is None:
            citation = Citation(len(self.items) + 1, label, document_id, page)
            self.by_key[key] = citation
            self.items.append(citation)
        if quote and quote not in citation.quotes and len(citation.quotes) < MAX_QUOTES:
            citation.quotes.append(quote)
        if part is not None and part not in citation.parts:
            citation.parts.append(part)


def citation_fragment(url: str) -> tuple[int | None, str | None]:
    """The 1-based page and the cited passage from a citation URL's fragment, when present.

    The passage is a text directive: [prefix-,]start[,end][,-suffix], each piece URL-encoded.
    Two remaining pieces are a start and an end; more mean the passage had unencoded commas."""
    fragment = urlsplit(url).fragment
    page_match = PAGE_FRAGMENT.search(fragment)
    page = int(page_match.group(1)) if page_match else None
    text_match = TEXT_FRAGMENT.search(fragment)
    if text_match is None:
        return (page or None), None
    pieces = text_match.group(1).split(",")
    if len(pieces) > 1 and pieces[0].endswith("-"):
        pieces = pieces[1:]
    if len(pieces) > 1 and pieces[-1].startswith("-"):
        pieces = pieces[:-1]
    joined = (" … " if len(pieces) == 2 else ",").join(unquote(piece) for piece in pieces)
    quote = " ".join(joined.split())[:MAX_QUOTE_CHARS]
    return (page or None), (quote or None)


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
    def __init__(self, client: Any, endpoint: Callable[[], str | None]) -> None:
        """``endpoint`` names the serving endpoint per question (None: not provisioned yet)."""
        self.api = client.api_client
        self.endpoint = endpoint

    def ask(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        endpoint = self.endpoint()
        if endpoint is None or ENDPOINT_NAME.fullmatch(endpoint) is None:
            raise ChatError(
                "CHAT_NOT_READY",
                "Document chat is still being set up. Try again in a few minutes.",
                503,
            )
        try:
            # return_trace adds the MLflow trace (id, spans) under databricks_output.trace.
            response: dict[str, Any] = self.api.do(
                "POST",
                f"/serving-endpoints/{endpoint}/invocations",
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
    """Local development: a canned answer that exercises tables, tools and citations. With a
    registered document and the mock source location, the citation links to that document."""

    def __init__(
        self,
        sources: SourceLocation | None = None,
        document: Callable[[], str | None] = lambda: None,
    ) -> None:
        self.sources = sources
        self.document = document

    def tag_trace(self, trace_id: str, tags: dict[str, str]) -> None:
        return None

    def ask(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        question = messages[-1]["content"]
        answer = (
            f"Mock answer to: {question}\n\n"
            "| Document | Field | Value |\n|---|---|---|\n"
            "| invoice.pdf | total | 540.00 |\n| invoice.pdf | seller_name | Northwind |"
        )
        annotation: dict[str, Any] = {"title": "invoice.pdf"}
        document_id = self.document() if self.sources else None
        if self.sources and document_id:
            folder = "/".join(
                (self.sources.catalog, self.sources.project_schema, self.sources.volume)
            )
            annotation = {
                "type": "url_citation",
                "title": f"{document_id}.pdf",
                "url": f"https://{self.sources.host}/Volumes/{folder}/incoming/{document_id}.pdf"
                "#page=1&text=Total",
            }
        return {
            "output": [
                {"type": "function_call", "name": "workspace__idp_mvp__idp_dev_chat_invoices"},
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": answer, "annotations": [annotation]}
                    ],
                },
            ]
        }


class ChatRepository(Protocol):
    def conversations(self, user: str) -> list[dict[str, Any]]: ...
    def messages(self, user: str, conversation_id: str) -> list[dict[str, Any]]: ...
    def append(
        self, user: str, conversation_id: str, messages: list[dict[str, Any]]
    ) -> int | None:
        """Insert messages whose (conversation_id, seq) is free, all or none, and return how
        many were inserted (None when unknown). A taken seq is skipped, not overwritten."""
        ...


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

    def append(
        self, user: str, conversation_id: str, messages: list[dict[str, Any]]
    ) -> int | None:
        with self.connect() as db:
            return db.executemany(
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
            ).rowcount


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

    def append(
        self, user: str, conversation_id: str, messages: list[dict[str, Any]]
    ) -> int | None:
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
        counts = run_with_retries(
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
        # After a retried attempt that had committed, this reads 0; the caller then reads back.
        return counts.get("num_inserted_rows")


class DocumentChatService:
    def __init__(
        self,
        client: ChatClient,
        repository: ChatRepository,
        file_names: Callable[[list[str]], dict[str, str]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sources: SourceLocation | None = None,
    ) -> None:
        self.client = client
        self.repository = repository
        self.file_names = file_names
        self.clock = clock
        self.sources = sources
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
        answer = parse_response(
            self.client.ask([*context, {"role": "user", "content": question}]), self.sources
        )
        labels = document_ids(answer.text, *(c.label for c in answer.citations))
        names = self._lookup(labels | {c.document_id for c in answer.citations if c.document_id})
        now = self.clock().isoformat()
        turn_id = str(uuid.uuid4())
        asked = {
            "seq": len(history),
            "role": "user",
            "text": question,
            "created_at": now,
            "turn_id": turn_id,
        }
        reply = {
            "seq": len(history) + 1,
            "role": "assistant",
            "text": rewrite_document_names(answer.text, names),
            "tools": answer.tools,
            "citations": [citation.public(names) for citation in answer.citations],
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "created_at": now,
            "turn_id": turn_id,
        }
        if any(citation.parts for citation in answer.citations):
            # The answer's text parts, so citation markers can follow the part that carried them.
            reply["parts"] = [rewrite_document_names(part, names) for part in answer.parts]
        if answer.trace_id:
            # Stored for operators (history row -> MLflow trace); never sent to the browser.
            reply["trace_id"] = answer.trace_id
            self._tag(answer.trace_id, user, conversation_id)
        self._store(user, conversation_id, [asked, reply])
        return {"conversation_id": conversation_id, "messages": [_public(asked), _public(reply)]}

    def _store(self, user: str, conversation_id: str, turn: list[dict[str, Any]]) -> None:
        """Append the turn after whatever is stored now. Another request on the same
        conversation (a second tab) may have taken these seqs since history was read; then the
        turn moves after it instead of being dropped."""
        for _ in range(2):
            if self.repository.append(user, conversation_id, turn) == len(turn):
                return
            stored = self.repository.messages(user, conversation_id)
            if any(message.get("turn_id") == turn[0]["turn_id"] for message in stored):
                return  # An earlier attempt committed before its answer was lost.
            after = stored[-1]["seq"] + 1 if stored else 0
            for offset, message in enumerate(turn):
                message["seq"] = after + offset
        raise ChatError(
            "CHAT_SAVE_CONFLICT",
            "This conversation changed in another tab. Reload it and ask again.",
            409,
        )

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


# Stored for operators and conflict checks; never sent to the browser.
PRIVATE_FIELDS = {"trace_id", "turn_id"}


def _public(message: dict[str, Any]) -> dict[str, Any]:
    """A history message as the browser sees it: operator-only fields removed."""
    return {key: value for key, value in message.items() if key not in PRIVATE_FIELDS}
