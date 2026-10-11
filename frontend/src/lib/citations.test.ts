import { describe, expect, it } from "vitest";

import type { ParsedElement } from "../components/DocumentViewer";
import type { ChatCitation } from "../types";
import { matchPassage, passageBoxes, sourceHref, textWithMarkers } from "./citations";

function element(element_id: number, content: string, page_id = 0): ParsedElement {
  return {
    element_id,
    element_type: "text",
    content,
    confidence: null,
    description: null,
    boxes: [{ page_id, x: 10 * element_id, y: 20, width: 100, height: 10 }],
  };
}

const elements = [
  element(1, "Invoice Date: 28-Jul-2011 INVOICE ID INV/06-92/543"),
  element(2, "Due Date : 03-Oct-1997"),
  element(3, "TOTAL : 888.55 EUR"),
  element(4, "Terms and Conditions"),
  element(5, "will be charged if payment is not made within the due date."),
  element(6, "TOTAL : 888.55 EUR", 1),
];

const citation = (overrides: Partial<ChatCitation> = {}): ChatCitation => ({
  number: 1,
  file_name: "invoice.pdf",
  document_id: "d0ed9896-da45-560a-8ddb-5b88d20dea1e",
  page: 1,
  quotes: ["TOTAL : 888.55 EUR"],
  parts: [0],
  ...overrides,
});

describe("matchPassage", () => {
  it("finds the element holding the passage, ignoring case and punctuation", () => {
    expect(matchPassage("total: 888.55 eur", elements, 0).map((e) => e.element_id)).toEqual([3]);
  });

  it("finds every element a longer passage spans", () => {
    const passage = "Terms and Conditions will be charged if payment is not made within the due date.";
    expect(matchPassage(passage, elements, 0).map((e) => e.element_id)).toEqual([4, 5]);
  });

  it("falls back to the element sharing most words, or nothing", () => {
    expect(matchPassage("Due date 03-Oct-1997 (final)", elements, 0).map((e) => e.element_id)).toEqual([2]);
    expect(matchPassage("Bank transfer reference", elements, 0)).toEqual([]);
  });

  it("only looks at the cited page", () => {
    expect(matchPassage("TOTAL : 888.55 EUR", elements, 1).map((e) => e.element_id)).toEqual([6]);
  });

  it("turns each found passage into one highlight box per element", () => {
    const boxes = passageBoxes(["TOTAL : 888.55 EUR", "total 888.55 EUR", "Due Date"], elements, 0);
    expect(boxes).toEqual([
      { page_id: 0, coord: [26, 16, 134, 34] },
      { page_id: 0, coord: [16, 16, 124, 34] },
    ]);
  });
});

describe("textWithMarkers", () => {
  it("puts each citation's marker after the text part that carried it", () => {
    const text = textWithMarkers(
      "Total is 888.55. Due 1997.",
      ["Total is 888.55.", " Due 1997.\n"],
      [citation(), citation({ number: 2, parts: [0, 1] })],
    );
    expect(text).toBe("Total is 888.55.12 Due 1997.2");
  });

  it("puts a marker after a table below it, and keeps text without parts as it is", () => {
    expect(textWithMarkers("x", ["| a |\n|---|\n| 1 |"], [citation()])).toBe("| a |\n|---|\n| 1 |\n1");
    expect(textWithMarkers("Plain answer.", undefined, [citation()])).toBe("Plain answer.");
  });
});

describe("sourceHref", () => {
  it("links to the source tab with the page, passages, number and answer", () => {
    expect(sourceHref(citation({ quotes: ["A b", "C&d"] }), "conv-1", 3)).toBe(
      "/sources/d0ed9896-da45-560a-8ddb-5b88d20dea1e?page=1&q=A+b&q=C%26d&n=1&c=conv-1&m=3",
    );
    expect(sourceHref(citation({ document_id: null }))).toBeNull();
  });
});
