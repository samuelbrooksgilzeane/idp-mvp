import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatPage } from "./ChatPage";

const CONVERSATION = "11111111-2222-3333-4444-555555555555";
const answer = {
  seq: 1,
  role: "assistant",
  text: "| Document | Field | Value |\n|---|---|---|\n| 50080tihd.pdf | voucher_total | 158072.31 |",
  tools: ["idp_dev_chat_document_fields"],
  citations: ["50080tihd.pdf"],
  created_at: "2026-10-01T10:00:00Z",
};

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/chat/:conversationId" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChatPage", () => {
  it("asks a question and shows the answer table, sources and tools as text", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (url === "/api/chat/messages") {
        expect(JSON.parse(String(init?.body))).toEqual({ conversation_id: null, text: "Fields of 50080tihd.pdf?" });
        return json({
          conversation_id: CONVERSATION,
          messages: [{ seq: 0, role: "user", text: "Fields of 50080tihd.pdf?", created_at: "" }, answer],
        });
      }
      return json({ conversations: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAt("/chat");

    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Fields of 50080tihd.pdf?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send question" }));

    expect(await screen.findByRole("cell", { name: "158072.31" })).toBeTruthy();
    expect(screen.getByText(/Sources:/).parentElement?.textContent).toContain("50080tihd.pdf");
    expect(screen.getByText(/Looked up with:/).parentElement?.textContent).toContain("idp_dev_chat_document_fields");
    expect(document.querySelectorAll(".chat-thread a")).toHaveLength(0);
  });

  it("numbers Knowledge Assistant citations and opens each cited page in a new tab", async () => {
    const DOC = "d0ed9896-da45-560a-8ddb-5b88d20dea1e";
    const cited = {
      seq: 1,
      role: "assistant",
      text: "The total is 888.55 EUR. It was due in 1997.",
      parts: ["The total is 888.55 EUR.", " It was due in 1997."],
      citations: [
        { number: 1, file_name: "invoice.pdf", document_id: DOC, page: 1, quotes: ["TOTAL : 888.55 EUR"], parts: [0] },
        { number: 2, file_name: "notes.pdf", document_id: null, page: null, quotes: [], parts: [1] },
      ],
      created_at: "",
    };
    vi.stubGlobal("fetch", vi.fn((url: string) => (url.startsWith("/api/chat/conversations/")
      ? json({ messages: [{ seq: 0, role: "user", text: "Total?", created_at: "" }, cited] })
      : json({ conversations: [] }))));
    renderAt(`/chat/${CONVERSATION}`);

    const marker = await screen.findByRole("link", { name: "Source 1: invoice.pdf, page 1" });
    const href = `/sources/${DOC}?page=1&q=TOTAL+%3A+888.55+EUR&n=1&c=${CONVERSATION}&m=1`;
    expect(marker).toHaveAttribute("href", href);
    expect(marker).toHaveAttribute("target", "_blank");
    expect(marker).toHaveAttribute("rel", "noopener noreferrer");
    expect(marker.closest("p")?.textContent).toBe("The total is 888.55 EUR.1 It was due in 1997.2");
    // A citation outside the source volume is numbered but not a link.
    expect(screen.getByLabelText("Source 2: notes.pdf").tagName).toBe("SPAN");

    const sources = screen.getByRole("list", { name: "Sources" });
    expect(sources).toHaveTextContent("invoice.pdf · page 1“TOTAL : 888.55 EUR”");
    expect(screen.getByRole("link", { name: "invoice.pdf · page 1" })).toHaveAttribute("href", href);
    expect(screen.queryByText(/Sources:/)).toBeNull();
  });

  it("loads an earlier conversation from the history list", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => url.startsWith("/api/chat/conversations/")
      ? json({ conversation_id: CONVERSATION, messages: [{ seq: 0, role: "user", text: "Earlier question", created_at: "" }, answer] })
      : json({ conversations: [{ conversation_id: CONVERSATION, title: "Earlier question", updated_at: "" }] })));
    renderAt(`/chat/${CONVERSATION}`);

    expect(await screen.findByRole("link", { name: "Earlier question" })).toBeTruthy();
    expect(await screen.findByRole("cell", { name: "voucher_total" })).toBeTruthy();
  });

  it("keeps the question and shows the error when asking fails", async () => {
    vi.stubGlobal("fetch", vi.fn((url: string) => url === "/api/chat/messages"
      ? json({ error: { code: "CHAT_ENDPOINT_FAILED", message: "The document assistant could not answer. Try again." } }, 502)
      : json({ conversations: [] })));
    renderAt("/chat");

    fireEvent.change(screen.getByLabelText("Question"), { target: { value: "Totals?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send question" }));

    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "The document assistant could not answer. Try again.");
    await waitFor(() => expect((screen.getByLabelText("Question") as HTMLTextAreaElement).value).toBe("Totals?"));
  });
});
