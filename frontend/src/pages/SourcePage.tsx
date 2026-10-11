import { ChevronRight, FileText, MessageSquare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";

import {
  DocumentViewer,
  type CitationTarget,
  type PageMetadata,
  type ParsedElement,
} from "../components/DocumentViewer";
import { Markdown } from "../components/Markdown";
import { numberedCitations, passageBoxes, textWithMarkers } from "../lib/citations";
import type { ChatMessage, DocumentRecord } from "../types";

type Turn = { question?: string; answer: ChatMessage };
type Located = { target: CitationTarget | null; found: boolean };

/**
 * A cited document page, opened in a new tab from a chat answer: /sources/:documentId with
 * page (1-based), q (each cited passage), n (citation number), c and m (the conversation and
 * answer it came from). Every page stays reachable; the passage is found in the cited page's
 * parsed text and highlighted there.
 */
export function SourcePage() {
  const { documentId = "" } = useParams();
  const [search] = useSearchParams();
  const page = positiveNumber(search.get("page"));
  const quotes = useMemo(() => search.getAll("q").filter(Boolean).slice(0, 5), [search]);
  const number = positiveNumber(search.get("n")) ?? 1;
  const conversationId = search.get("c");
  const seq = positiveNumber(search.get("m"));

  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [located, setLocated] = useState<Located | null>(null);
  const [turn, setTurn] = useState<Turn | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/documents/${encodeURIComponent(documentId)}`, { signal: controller.signal })
      .then(async (response) => {
        if (response.status === 404) throw new Error("This document is no longer available.");
        if (!response.ok) throw new Error("The document could not be loaded. Reload to try again.");
        setDocument((await response.json()) as DocumentRecord);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setFailure((reason as Error).message);
      });
    return () => controller.abort();
  }, [documentId]);

  useEffect(() => {
    const controller = new AbortController();
    locate(documentId, page, quotes, number, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setLocated(value); })
      .catch(() => { if (!controller.signal.aborted) setLocated({ target: null, found: false }); });
    return () => controller.abort();
  }, [documentId, page, quotes, number]);

  useEffect(() => {
    if (!conversationId || seq === null) return;
    const controller = new AbortController();
    fetch(`/api/chat/conversations/${encodeURIComponent(conversationId)}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) return;
        const { messages } = (await response.json()) as { messages: ChatMessage[] };
        const answer = messages.find((message) => message.seq === seq && message.role === "assistant");
        const question = messages.find((message) => message.seq === seq - 1 && message.role === "user");
        if (answer) setTurn({ answer, question: question?.text });
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [conversationId, seq]);

  useEffect(() => {
    if (document) window.document.title = `${document.file_name}${page ? ` · page ${page}` : ""}`;
  }, [document, page]);

  const answerCitations = turn ? numberedCitations(turn.answer) : [];

  return (
    <div className="source-page">
      <header className="source-header">
        <span className="brand-mark" aria-hidden="true">IDP</span>
        <nav className="source-crumbs" aria-label="Location">
          <span>IDP MVP</span>
          <ChevronRight size={14} aria-hidden="true" />
          <strong>Cited source</strong>
        </nav>
      </header>
      <div className="source-main">
        <div className="source-viewer">
          {failure ? (
            <p className="source-failure" role="alert">{failure}</p>
          ) : (
            <DocumentViewer
              variant="source"
              documentId={documentId}
              documentStatus={document?.status ?? "PARSED"}
              parseRunId={located?.target?.parseRunId}
              sourcePending={located === null}
              citationTarget={located?.target ?? null}
            />
          )}
        </div>
        <aside className="source-panel" aria-label="Citation details">
          <div>
            <p className="source-eyebrow">Citation [{number}]</p>
            <div className="source-file">
              <FileText size={18} aria-hidden="true" />
              <div>
                <h1>{document?.file_name ?? (failure ? "Document unavailable" : "Loading…")}</h1>
                <div className="source-meta">
                  <span className="source-pill">{page ? `Page ${page}` : "Page not given"}</span>
                  {document?.case_id ? <span className="source-pill">Case {document.case_id}</span> : null}
                </div>
              </div>
            </div>
          </div>
          {quotes.length ? (
            <div>
              <p className="source-eyebrow">{quotes.length === 1 ? "Cited passage" : "Cited passages"}</p>
              <div className="source-quotes">
                {quotes.map((quote) => <blockquote key={quote}>{quote}</blockquote>)}
              </div>
              {located && !located.found && located.target ? (
                <p className="source-hint">
                  The passage could not be found in this page's parsed text, so the page is shown without a highlight.
                </p>
              ) : null}
            </div>
          ) : null}
          {turn ? (
            <div>
              <p className="source-eyebrow">From the answer</p>
              <div className="source-answer">
                {turn.question ? (
                  <p className="source-question"><MessageSquare size={14} aria-hidden="true" />{turn.question}</p>
                ) : null}
                <Markdown
                  text={textWithMarkers(turn.answer.text, turn.answer.parts, answerCitations)}
                  renderMarker={(marker) => (
                    <span className={marker === number ? "citation-marker" : "citation-marker plain"}>{marker}</span>
                  )}
                />
              </div>
            </div>
          ) : null}
          <p className="source-note">
            The passage is found by matching the Knowledge Assistant's citation to the page's parsed text. Anyone
            who can use the app can open this page; no access to the files is needed.
          </p>
        </aside>
      </div>
    </div>
  );
}

/** The cited page of the latest parse and highlight boxes for the passages found on it. */
async function locate(
  documentId: string,
  page: number | null,
  quotes: string[],
  number: number,
  signal: AbortSignal,
): Promise<Located> {
  const response = await fetch(`/api/documents/${encodeURIComponent(documentId)}/viewer`, { signal });
  if (!response.ok) return { target: null, found: false };
  const payload = (await response.json()) as { parse_run_id?: string; pages?: PageMetadata[] };
  const pages = payload.pages ?? [];
  const cited = pages.find((item) => item.page_number === page) ?? pages[0];
  if (!cited) return { target: null, found: false };
  const query = new URLSearchParams({ page_id: String(cited.page_id) });
  if (payload.parse_run_id) query.set("parse_run_id", payload.parse_run_id);
  const elements = await fetch(`/api/documents/${encodeURIComponent(documentId)}/elements?${query}`, { signal })
    .then((result) => (result.ok ? (result.json() as Promise<ParsedElement[]>) : []))
    .catch(() => [] as ParsedElement[]);
  const boxes = passageBoxes(quotes, elements, cited.page_id);
  return {
    target: {
      parseRunId: payload.parse_run_id,
      pageId: cited.page_id,
      fieldLabel: `[${number}] Cited passage`,
      boxes,
      nonce: 1,
    },
    found: boxes.length > 0,
  };
}

function positiveNumber(value: string | null): number | null {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}
