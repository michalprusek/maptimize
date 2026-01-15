/**
 * Image Editor Geometry Utilities
 *
 * Functions for bbox hit testing, handle positions, and coordinate transformations.
 */

import type { EditorBbox, HandlePosition, Point, Rect } from "./types";
import { HANDLE_SIZE, HANDLE_HIT_PADDING, MIN_BBOX_SIZE } from "./constants";

/**
 * Get the positions of all 8 resize handles for a bbox.
 */
export function getHandlePositions(
  bbox: Rect,
  scale: number = 1
): Record<HandlePosition, Point> {
  const { x, y, width, height } = bbox;
  const sx = x * scale;
  const sy = y * scale;
  const sw = width * scale;
  const sh = height * scale;

  return {
    nw: { x: sx, y: sy },
    n: { x: sx + sw / 2, y: sy },
    ne: { x: sx + sw, y: sy },
    w: { x: sx, y: sy + sh / 2 },
    e: { x: sx + sw, y: sy + sh / 2 },
    sw: { x: sx, y: sy + sh },
    s: { x: sx + sw / 2, y: sy + sh },
    se: { x: sx + sw, y: sy + sh },
  };
}

/**
 * Check if a point is within a handle's hit area.
 */
export function isPointInHandle(
  point: Point,
  handleCenter: Point,
  handleSize: number = HANDLE_SIZE
): boolean {
  const hitSize = handleSize + HANDLE_HIT_PADDING * 2;
  const halfHit = hitSize / 2;

  return (
    point.x >= handleCenter.x - halfHit &&
    point.x <= handleCenter.x + halfHit &&
    point.y >= handleCenter.y - halfHit &&
    point.y <= handleCenter.y + halfHit
  );
}

/**
 * Find which handle (if any) is at a given point.
 * Only checks corner handles (nw, ne, sw, se).
 */
export function getHandleAtPosition(
  point: Point,
  bbox: Rect,
  scale: number = 1
): HandlePosition | null {
  const handles = getHandlePositions(bbox, scale);
  // Only corner handles
  const cornerKeys: HandlePosition[] = ["nw", "ne", "sw", "se"];

  for (const position of cornerKeys) {
    const center = handles[position];
    if (isPointInHandle(point, center)) {
      return position;
    }
  }

  return null;
}

/**
 * Check if a point is inside a bbox.
 */
export function isPointInBbox(point: Point, bbox: Rect, scale: number = 1): boolean {
  const sx = bbox.x * scale;
  const sy = bbox.y * scale;
  const sw = bbox.width * scale;
  const sh = bbox.height * scale;

  return (
    point.x >= sx &&
    point.x <= sx + sw &&
    point.y >= sy &&
    point.y <= sy + sh
  );
}

/**
 * Find the topmost bbox at a given point.
 * Returns the last matching bbox (rendered on top).
 */
export function findBboxAtPosition(
  point: Point,
  bboxes: EditorBbox[],
  scale: number = 1
): EditorBbox | null {
  // Iterate in reverse to find topmost (last rendered)
  for (let i = bboxes.length - 1; i >= 0; i--) {
    const bbox = bboxes[i];
    if (isPointInBbox(point, bbox, scale)) {
      return bbox;
    }
  }
  return null;
}

/**
 * Calculate new bbox dimensions after resizing from a handle.
 */
export function resizeBbox(
  original: Rect,
  handle: HandlePosition,
  delta: Point,
  scale: number = 1,
  imageWidth: number,
  imageHeight: number
): Rect {
  // Convert delta from screen to image coordinates
  const dx = delta.x / scale;
  const dy = delta.y / scale;

  let { x, y, width, height } = original;

  switch (handle) {
    case "nw":
      x += dx;
      y += dy;
      width -= dx;
      height -= dy;
      break;
    case "n":
      y += dy;
      height -= dy;
      break;
    case "ne":
      y += dy;
      width += dx;
      height -= dy;
      break;
    case "w":
      x += dx;
      width -= dx;
      break;
    case "e":
      width += dx;
      break;
    case "sw":
      x += dx;
      width -= dx;
      height += dy;
      break;
    case "s":
      height += dy;
      break;
    case "se":
      width += dx;
      height += dy;
      break;
  }

  // Enforce minimum size
  if (width < MIN_BBOX_SIZE) {
    if (handle.includes("w")) {
      x = original.x + original.width - MIN_BBOX_SIZE;
    }
    width = MIN_BBOX_SIZE;
  }
  if (height < MIN_BBOX_SIZE) {
    if (handle.includes("n")) {
      y = original.y + original.height - MIN_BBOX_SIZE;
    }
    height = MIN_BBOX_SIZE;
  }

  // Clamp to image bounds
  x = Math.max(0, Math.min(x, imageWidth - MIN_BBOX_SIZE));
  y = Math.max(0, Math.min(y, imageHeight - MIN_BBOX_SIZE));
  width = Math.min(width, imageWidth - x);
  height = Math.min(height, imageHeight - y);

  return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) };
}

/**
 * Calculate new bbox position after moving.
 */
export function moveBbox(
  original: Rect,
  delta: Point,
  scale: number = 1,
  imageWidth: number,
  imageHeight: number
): Rect {
  const dx = delta.x / scale;
  const dy = delta.y / scale;

  let x = original.x + dx;
  let y = original.y + dy;

  // Clamp to image bounds
  x = Math.max(0, Math.min(x, imageWidth - original.width));
  y = Math.max(0, Math.min(y, imageHeight - original.height));

  return {
    x: Math.round(x),
    y: Math.round(y),
    width: original.width,
    height: original.height,
  };
}

/**
 * Create a bbox from two corner points (for drawing new bboxes).
 */
export function createBboxFromCorners(
  start: Point,
  end: Point,
  scale: number = 1,
  imageWidth: number,
  imageHeight: number
): Rect | null {
  // Convert to image coordinates
  let x1 = start.x / scale;
  let y1 = start.y / scale;
  let x2 = end.x / scale;
  let y2 = end.y / scale;

  // Normalize to ensure x1,y1 is top-left
  const minX = Math.min(x1, x2);
  const minY = Math.min(y1, y2);
  const maxX = Math.max(x1, x2);
  const maxY = Math.max(y1, y2);

  const width = maxX - minX;
  const height = maxY - minY;

  // Check minimum size
  if (width < MIN_BBOX_SIZE || height < MIN_BBOX_SIZE) {
    return null;
  }

  // Clamp to image bounds
  const x = Math.max(0, Math.min(minX, imageWidth - MIN_BBOX_SIZE));
  const y = Math.max(0, Math.min(minY, imageHeight - MIN_BBOX_SIZE));
  const clampedWidth = Math.min(width, imageWidth - x);
  const clampedHeight = Math.min(height, imageHeight - y);

  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(clampedWidth),
    height: Math.round(clampedHeight),
  };
}

/**
 * Get cursor style for a handle position.
 */
export function getCursorForHandle(handle: HandlePosition | null): string {
  if (!handle) return "default";

  const cursors: Record<HandlePosition, string> = {
    nw: "nwse-resize",
    n: "ns-resize",
    ne: "nesw-resize",
    w: "ew-resize",
    e: "ew-resize",
    sw: "nesw-resize",
    s: "ns-resize",
    se: "nwse-resize",
  };

  return cursors[handle];
}

/**
 * Convert canvas coordinates to image coordinates.
 */
export function canvasToImage(
  canvasPoint: Point,
  scale: number,
  panOffset: Point
): Point {
  return {
    x: (canvasPoint.x - panOffset.x) / scale,
    y: (canvasPoint.y - panOffset.y) / scale,
  };
}

/**
 * Convert image coordinates to canvas coordinates.
 */
export function imageToCanvas(
  imagePoint: Point,
  scale: number,
  panOffset: Point
): Point {
  return {
    x: imagePoint.x * scale + panOffset.x,
    y: imagePoint.y * scale + panOffset.y,
  };
}

/**
 * Calculate scale to fit image within container.
 */
export function calculateFitScale(
  imageWidth: number,
  imageHeight: number,
  containerWidth: number,
  containerHeight: number,
  padding: number = 40
): number {
  const availableWidth = containerWidth - padding * 2;
  const availableHeight = containerHeight - padding * 2;

  const scaleX = availableWidth / imageWidth;
  const scaleY = availableHeight / imageHeight;

  return Math.min(scaleX, scaleY, 1); // Don't upscale beyond 100%
}

/**
 * Calculate pan offset to center image in container.
 */
export function calculateCenterOffset(
  imageWidth: number,
  imageHeight: number,
  containerWidth: number,
  containerHeight: number,
  scale: number
): Point {
  const scaledWidth = imageWidth * scale;
  const scaledHeight = imageHeight * scale;

  return {
    x: (containerWidth - scaledWidth) / 2,
    y: (containerHeight - scaledHeight) / 2,
  };
}

/**
 * Object-cover transformation parameters for fitting content into a container.
 * Uses the smaller dimension to fill the container (like CSS object-cover).
 */
export interface ObjectCoverTransform {
  scale: number;
  offsetX: number;
  offsetY: number;
}

/**
 * Calculate object-cover transformation for fitting a bbox into a square container.
 * The smaller bbox dimension fills the container, larger dimension overflows and is centered.
 */
export function calculateObjectCoverTransform(
  bboxWidth: number,
  bboxHeight: number,
  containerSize: number
): ObjectCoverTransform {
  const scale = containerSize / Math.min(bboxWidth, bboxHeight);
  const offsetX = (containerSize - bboxWidth * scale) / 2;
  const offsetY = (containerSize - bboxHeight * scale) / 2;
  return { scale, offsetX, offsetY };
}

/**
 * Build an SVG path from polygon points, transforming to percentage coordinates.
 * Used for polygon overlays on thumbnails with object-cover behavior.
 *
 * @param points - Polygon points in source coordinates
 * @param transform - Object-cover transformation parameters
 * @param containerSize - Container size for percentage calculation
 * @param originOffset - Optional offset to subtract from points (for FOV to bbox-relative conversion)
 */
export function buildPolygonSvgPath(
  points: [number, number][],
  transform: ObjectCoverTransform,
  containerSize: number,
  originOffset: { x: number; y: number } = { x: 0, y: 0 }
): string {
  return points
    .map((p, i) => {
      const rawX = (p[0] - originOffset.x) * transform.scale + transform.offsetX;
      const rawY = (p[1] - originOffset.y) * transform.scale + transform.offsetY;
      const pctX = (rawX / containerSize) * 100;
      const pctY = (rawY / containerSize) * 100;
      return `${i === 0 ? "M" : "L"} ${pctX} ${pctY}`;
    })
    .join(" ") + " Z";
}

/**
 * Normalize polygon data from API to consistent multi-polygon format.
 *
 * API can return either:
 * - Single polygon: [[x,y], [x,y], ...]
 * - Multi-polygon: [[[x,y], ...], [[x,y], ...], ...]
 *
 * This function always returns the multi-polygon format.
 */
export function normalizePolygonData(
  data: unknown
): [number, number][][] | null {
  if (!data || !Array.isArray(data) || data.length === 0) {
    return null;
  }

  const firstElement = data[0];

  // Check if it's multi-polygon format (first element is an array of arrays)
  const isMultiPolygon = Array.isArray(firstElement) &&
    firstElement.length > 0 &&
    Array.isArray(firstElement[0]);

  if (isMultiPolygon) {
    // Already multi-polygon format
    return data as [number, number][][];
  }

  // Single polygon format - wrap in array
  return [data as [number, number][]];
}
