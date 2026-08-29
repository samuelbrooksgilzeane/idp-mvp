import type { ElementBox } from "./DocumentViewer";

type Size = { width: number; height: number };

export type CitationCoordinate = {
  page_id: number;
  coord: number[];
};

/**
 * Convert a citation coordinate into the same {x, y, width, height} box contract the parse
 * overlay uses, so citation boxes rescale on zoom and resize through `scaleBoundingBox`.
 *
 * Two shapes occur and both must be read the same way the backend reads them: `ai_extract`
 * returns a four-value rectangle [x1, y1, x2, y2], while a parser may return an even-length
 * polygon of corner points. Returns null when the coordinate cannot form a positive area.
 */
export function citationToBox(citation: CitationCoordinate): ElementBox | null {
  const values = citation.coord;
  let x1: number, y1: number, x2: number, y2: number;
  if (values.length === 4) {
    [x1, y1, x2, y2] = values;
  } else if (values.length >= 8 && values.length % 2 === 0) {
    const xs = values.filter((_, index) => index % 2 === 0);
    const ys = values.filter((_, index) => index % 2 === 1);
    x1 = Math.min(...xs);
    x2 = Math.max(...xs);
    y1 = Math.min(...ys);
    y2 = Math.max(...ys);
  } else {
    return null;
  }
  if (x2 <= x1 || y2 <= y1) return null;
  return { page_id: citation.page_id, x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
}

export function scaleBoundingBox(
  box: ElementBox,
  source: Size,
  rendered: Size,
): ElementBox {
  if (!source.width || !source.height || !rendered.width || !rendered.height) return box;
  const scaleX = rendered.width / source.width;
  const scaleY = rendered.height / source.height;
  return {
    ...box,
    x: box.x * scaleX,
    y: box.y * scaleY,
    width: box.width * scaleX,
    height: box.height * scaleY,
  };
}
