import { MessageSquarePlus, SendHorizontal } from "lucide-react";
import { type FormEvent, type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Markdown } from "../components/Markdown";
import type { ChatMessage, ConversationSummary } from "../types";

type AskResponse = { conversation_id: string; messages: ChatMessage[] };

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(body?.error?.message ?? body?.detail?.[0]?.msg ?? "The request failed. Try again.");
  }
  return body as T;
}

/** Ask the document assistant; history is private to the signed-in user. */
export function ChatPage() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const skipLoad = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const loadConversations = useCallback(async (signal?: AbortSignal) => {
    try {
      const body = await requestJson<{ conversations: ConversationSummary[] }>("/api/chat/conversations", { signal });
      setConversations(body.conversations);
    } catch (reason) {
      if (!signal?.aborted) setError((reason as Error).message);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadConversations(controller.signal);
    return () => controller.abort();
  }, [loadConversations]);

  useEffect(() => {
    setError(null);
    if (!conversationId) {
      setMessages([]);
      return;
    }
    if (skipLoad.current === conversationId) return; // just created here; messages already shown
    const controller = new AbortController();
    requestJson<{ messages: ChatMessage[] }>(`/api/chat/conversations/${encodeURIComponent(conversationId)}`, { signal: controller.signal })
      .then((body) => setMessages(body.messages))
      .catch((reason) => { if (!controller.signal.aborted) setError((reason as Error).message); });
    return () => controller.abort();
  }, [conversationId]);

  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [messages, pending]);

  async function ask(event?: FormEvent) {
    event?.preventDefault();
    const text = draft.trim();
    if (!text || pending) return;
    setPending(text);
    setDraft("");
    setError(null);
    try {
      const body = await requestJson<AskResponse>("/api/chat/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationId ?? null, text }),
      });
      setMessages((current) => [...current, ...body.messages]);
      if (body.conversation_id !== conversationId) {
        skipLoad.current = body.conversation_id;
        navigate(`/chat/${body.conversation_id}`, { replace: !conversationId });
      }
      void loadConversations();
    } catch (reason) {
      setDraft(text);
      setError((reason as Error).message);
    } finally {
      setPending(null);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask();
    }
  }

  return (
    <section className="chat-layout" aria-label="Ask documents">
      <aside className="chat-history" aria-label="Your conversations">
        <Link to="/chat" className="chat-new"><MessageSquarePlus size={15} aria-hidden="true" />New conversation</Link>
        {conversations.length === 0 ? <p className="chat-muted">No conversations yet.</p> : (
          <ul>
            {conversations.map((item) => (
              <li key={item.conversation_id}>
                <Link to={`/chat/${item.conversation_id}`}
                  className={item.conversation_id === conversationId ? "active" : undefined}>
                  {item.title || "Untitled"}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <div className="chat-main">
        <div className="page-heading">
          <div>
            <h1>Ask documents</h1>
            <p>Questions about your documents and their extracted data. Extracted values are model output, not approved.</p>
          </div>
        </div>
        <div className="chat-thread" aria-live="polite">
          {messages.length === 0 && !pending ? (
            <div className="page-state">
              <strong>Ask about invoices, forms or extracted fields</strong>
              <span>For example: “Invoice totals by supplier” or “Show the extracted fields of 50080tihd.pdf”.</span>
            </div>
          ) : null}
          {messages.map((message) => <Message key={message.seq} message={message} />)}
          {pending ? (
            <>
              <Message message={{ seq: -1, role: "user", text: pending, created_at: "" }} />
              <p className="chat-thinking" role="status">Looking through your documents… this can take up to a minute.</p>
            </>
          ) : null}
          <div ref={end} />
        </div>
        {error ? <p className="chat-error" role="alert">{error}</p> : null}
        <form className="chat-composer" onSubmit={(event) => void ask(event)}>
          <label htmlFor="chat-question" className="visually-hidden">Question</label>
          <textarea id="chat-question" value={draft} maxLength={4000} rows={2}
            placeholder="Ask a question (Enter to send, Shift+Enter for a new line)"
            onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} disabled={Boolean(pending)} />
          <button type="submit" disabled={!draft.trim() || Boolean(pending)} aria-label="Send question">
            <SendHorizontal size={16} aria-hidden="true" />
          </button>
        </form>
      </div>
    </section>
  );
}

function Message({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return <div className="chat-message chat-user"><p>{message.text}</p></div>;
  }
  return (
    <div className="chat-message chat-assistant">
      <Markdown text={message.text} />
      {message.citations?.length ? (
        <p className="chat-meta"><span>Sources:</span> {message.citations.join(", ")}</p>
      ) : null}
      {message.tools?.length ? (
        <p className="chat-meta"><span>Looked up with:</span> {message.tools.join(", ")}</p>
      ) : null}
    </div>
  );
}
