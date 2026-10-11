import type { ParsedElement } from "../components/DocumentViewer";
import type { CitationCoordinate } from "../components/viewerGeometry";
import type { ChatCitation, ChatMessage } from "../types";

// Private-use characters around a citation number; the Markdown renderer draws them as markers.
const MARKER_START = "\uE000";
const MARKER_END = "\uE001";
export const MARKER = /\uE000(\d+)\uE001/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;

/** Numbered citations; answers saved before them carry plain labels instead. */
export function numberedCitations(message: ChatMessage): ChatCitation[] {
  return (message.citations ?? []).filter((item): item is ChatCitation => typeof item === "object");
}

/** The answer text with a marker after each text part that carried a citation. */
export function textWithMarkers(text: string, parts: string[] | undefined, citations: ChatCitation[]): string {
  if (!parts?.length || !citations.some((citation) => citation.parts.length)) return text;
  return parts.map((part, index) => {
    const numbers = citations.filter((citation) => citation.parts.includes(index)).map((c) => c.number);
    if (!numbers.length) return part;
    const markers = numbers.map((number) => `${MARKER_START}${number}${MARKER_END}`).join("");
    const trailing = /\s*$/.exec(part)?.[0] ?? "";
    const body = part.slice(0, part.length - trailing.length);
    // A marker after a table row would stop the row from parsing as a table; put it below.
    return TABLE_ROW.test(body.split("\n").at(-1) ?? "") ? `${body}\n${markers}${trailing}` : `${body}${markers}${trailing}`;
  }).join("").trim();
}

/** The source tab for a citation: its document at the cited page, with the passages to find. */
export function sourceHref(citation: ChatCitation, conversationId?: string, seq?: number): string | null {
  if (!citation.document_id) return null;
  const query = new URLSearchParams();
  if (citation.page) query.set("page", String(citation.page));
  citation.quotes.forEach((quote) => query.append("q", quote));
  query.set("n", String(citation.number));
  if (conversationId) query.set("c", conversationId);
  if (seq !== undefined) query.set("m", String(seq));
  return `/sources/${encodeURIComponent(citation.document_id)}?${query}`;
}

const normalize = (text: string) =>
  text.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

/**
 * The parsed elements of one page that hold a cited passage, compared word by word ignoring case
 * and punctuation: the smallest element containing the whole passage, else the elements the
 * passage spans, else the element sharing most of its words (at least 60%). Empty when none fit.
 */
export function matchPassage(passage: string, elements: ParsedElement[], pageId: number): ParsedElement[] {
  const wanted = normalize(passage);
  if (!wanted) return [];
  const candidates = elements
    .filter((element) => element.boxes.some((box) => box.page_id === pageId))
    .map((element) => ({ element, text: normalize(element.content ?? "") }))
    .filter((candidate) => candidate.text);
  const containing = candidates
    .filter((candidate) => ` ${candidate.text} `.includes(` ${wanted} `))
    .sort((a, b) => a.text.length - b.text.length);
  if (containing.length) return [containing[0].element];
  const spanned = candidates.filter(
    (candidate) => candidate.text.includes(" ") && ` ${wanted} `.includes(` ${candidate.text} `),
  );
  if (spanned.length) return spanned.map((candidate) => candidate.element);
  const words = wanted.split(" ");
  let best: { element: ParsedElement; share: number } | null = null;
  for (const candidate of candidates) {
    const own = new Set(candidate.text.split(" "));
    const share = words.filter((word) => own.has(word)).length / words.length;
    if (share >= 0.6 && (best === null || share > best.share)) best = { element: candidate.element, share };
  }
  return best ? [best.element] : [];
}

// Page-image pixels around a highlight, so the box does not clip the text the parser outlined.
const HIGHLIGHT_PADDING = 4;

/** Highlight boxes on one page for every cited passage found there. */
export function passageBoxes(passages: string[], elements: ParsedElement[], pageId: number): CitationCoordinate[] {
  const seen = new Set<number>();
  const boxes: CitationCoordinate[] = [];
  for (const passage of passages) {
    for (const element of matchPassage(passage, elements, pageId)) {
      if (seen.has(element.element_id)) continue;
      seen.add(element.element_id);
      for (const box of element.boxes) {
        if (box.page_id === pageId) {
          const pad = HIGHLIGHT_PADDING;
          boxes.push({
            page_id: pageId,
            coord: [Math.max(box.x - pad, 0), Math.max(box.y - pad, 0), box.x + box.width + pad, box.y + box.height + pad],
          });
        }
      }
    }
  }
  return boxes;
}
