import type { ElementBox } from "./DocumentViewer";

type Size = { width: number; height: number };

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
