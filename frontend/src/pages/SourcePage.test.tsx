import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SourcePage } from "./SourcePage";

const DOC = "d0ed9896-da45-560a-8ddb-5b88d20dea1e";
const CONVERSATION = "11111111-2222-3333-4444-555555555555";
const pages = [
  { page_id: 0, page_number: 1, element_count: 1, element_types: ["text"], image_url: `/api/documents/${DOC}/pages/0/image` },
  { page_id: 1, page_number: 2, element_count: 2, element_types: ["text"], image_url: `/api/documents/${DOC}/pages/1/image` },
];
const pageTwo = [
  { element_id: 3, element_type: "text", content: "Due Date : 03-Oct-1997", confidence: null, description: null, boxes: [{ page_id: 1, x: 10, y: 100, width: 300, height: 30 }] },
  { element_id: 4, element_type: "text", content: "TOTAL : 888.55 EUR", confidence: null, description: null, boxes: [{ page_id: 1, x: 500, y: 800, width: 260, height: 30 }] },
];

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

function serve(documentStatus = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = input.toString();
    if (url === `/api/documents/${DOC}`) {
      return documentStatus === 200
        ? json({ document_id: DOC, file_name: "invoice.pdf", case_id: "CASE-7", status: "PARSED" })
        : json({ error: { message: "Not found" } }, documentStatus);
    }
    if (url.startsWith(`/api/documents/${DOC}/viewer`)) return json({ parse_run_id: "run-1", pages });
    if (url.includes("/elements?")) return json(url.includes("page_id=1") ? pageTwo : []);
    if (url === `/api/chat/conversations/${CONVERSATION}`) {
      return json({
        messages: [
          { seq: 0, role: "user", text: "What is the total?", created_at: "" },
          {
            seq: 1, role: "assistant", text: "It totals 888.55 EUR.", parts: ["It totals 888.55 EUR."], created_at: "",
            citations: [{ number: 1, file_name: "invoice.pdf", document_id: DOC, page: 2, quotes: ["TOTAL : 888.55 EUR"], parts: [0] }],
          },
        ],
      });
    }
    return json({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function open(query: string) {
  return render(
    <MemoryRouter initialEntries={[`/sources/${DOC}?${query}`]}>
      <Routes><Route path="/sources/:documentId" element={<SourcePage />} /></Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SourcePage", () => {
  it("opens the cited page, highlights the passages and shows the answer they support", async () => {
    const fetchMock = serve();
    open(`page=2&q=TOTAL+%3A+888.55+EUR&q=Due+Date&n=1&c=${CONVERSATION}&m=1`);

    expect(await screen.findByRole("heading", { name: "invoice.pdf" })).toBeTruthy();
    expect(screen.getByText("Page 2")).toBeTruthy();
    expect(screen.getByText("Case CASE-7")).toBeTruthy();
    expect(screen.getByText("Cited passages")).toBeTruthy();
    expect(screen.getByText("TOTAL : 888.55 EUR", { selector: "blockquote" })).toBeTruthy();
    expect(await screen.findByText("What is the total?")).toBeTruthy();
    expect(screen.getByText("It totals 888.55 EUR.").closest(".source-answer")?.textContent).toContain("888.55 EUR.1");

    const page = await screen.findByAltText("Rendered page 2");
    Object.defineProperties(page, {
      naturalWidth: { value: 1000 }, naturalHeight: { value: 1400 }, clientWidth: { value: 500 }, clientHeight: { value: 700 },
    });
    fireEvent.load(page);
    expect(await screen.findAllByRole("img", { name: /Cited evidence for \[1\] Cited passage on page 2/ })).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/documents/${DOC}/elements?page_id=1&parse_run_id=run-1`, expect.anything(),
    );
    expect(window.document.title).toBe("invoice.pdf · page 2");
  });

  it("says when the passage is not in the page's parsed text", async () => {
    serve();
    open("page=2&q=Bank+transfer+reference&n=3");
    expect(await screen.findByText(/could not be found in this page's parsed text/)).toBeTruthy();
    expect(screen.getByText("Citation [3]")).toBeTruthy();
  });

  it("explains a document that no longer exists", async () => {
    serve(404);
    open("page=1&q=Total");
    expect(await screen.findByRole("alert")).toHaveTextContent("This document is no longer available.");
  });
});
