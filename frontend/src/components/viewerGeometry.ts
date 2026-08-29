import type { ElementBox } from "./DocumentViewer";

type Size = { width: number; height: number };

export type CitationCoordinate = {
  page_id: number;
  coord: [number, number, number, number];
};

/**
 * Convert an `ai_extract` citation coordinate ([x1, y1, x2, y2] in natural page-image
 * pixels) into the same {x, y, width, height} box contract the parse overlay uses, so
 * citation boxes rescale on zoom and resize through `scaleBoundingBox`.
 */
export function citationToBox(citation: CitationCoordinate): ElementBox {
  const [x1, y1, x2, y2] = citation.coord;
  return {
    page_id: citation.page_id,
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1),
  };
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
