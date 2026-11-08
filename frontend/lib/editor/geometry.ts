/**
 * Image Editor Geometry Utilities
 *
 * Functions for bbox hit testing, handle positions, and coordinate transformations.
 */

import type { EditorBbox, HandlePosition, Point, Rect } from "./types";
import {
  HANDLE_SIZE,
  HANDLE_HIT_PADDING,
  MIN_BBOX_SIZE,
  ROTATION_HANDLE_DISTANCE,
} from "./constants";

// ============================================================================
// Rotation helpers (SSOT). Positive angle matches ctx.rotate(angleRad): clockwise
// in the y-down canvas. Geometry rotates by +angle; hit-testing un-rotates by -angle.
// ============================================================================

/** Rotate a vector by `angleDeg` (about the origin). */
export function rotateVec(vx: number, vy: number, angleDeg: number): Point {
  const r = (angleDeg * Math.PI) / 180;
  const c = Math.cos(r);
  const s = Math.sin(r);
  return { x: vx * c - vy * s, y: vx * s + vy * c };
}

/** Rotate a point about `center` by `angleDeg`. */
export function rotatePointAbout(p: Point, center: Point, angleDeg: number): Point {
  const v = rotateVec(p.x - center.x, p.y - center.y, angleDeg);
  return { x: center.x + v.x, y: center.y + v.y };
}

/** Scaled (screen-space) centre of a bbox. */
export function bboxCenter(bbox: Rect, scale: number = 1): Point {
  return { x: (bbox.x + bbox.width / 2) * scale, y: (bbox.y + bbox.height / 2) * scale };
}

/**
 * Get the positions of all 8 resize handles for a bbox, rotated about its centre
 * when the bbox has an angle.
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

  const raw: Record<HandlePosition, Point> = {
    nw: { x: sx, y: sy },
    n: { x: sx + sw / 2, y: sy },
    ne: { x: sx + sw, y: sy },
    w: { x: sx, y: sy + sh / 2 },
    e: { x: sx + sw, y: sy + sh / 2 },
    sw: { x: sx, y: sy + sh },
    s: { x: sx + sw / 2, y: sy + sh },
    se: { x: sx + sw, y: sy + sh },
  };

  const angle = bbox.angle ?? 0;
  if (!angle) return raw;

  const center = bboxCenter(bbox, scale);
  const out = {} as Record<HandlePosition, Point>;
  (Object.keys(raw) as HandlePosition[]).forEach((k) => {
    out[k] = rotatePointAbout(raw[k], center, angle);
  });
  return out;
}

/**
 * Screen-space position of the rotation handle (above the box's top edge,
 * following the box rotation).
 */
export function getRotationHandlePosition(bbox: Rect, scale: number = 1): Point {
  const center = bboxCenter(bbox, scale);
  const dist = (bbox.height * scale) / 2 + ROTATION_HANDLE_DISTANCE;
  const v = rotateVec(0, -dist, bbox.angle ?? 0);
  return { x: center.x + v.x, y: center.y + v.y };
}

/** Whether a point hits the rotation handle. */
export function isPointInRotationHandle(
  point: Point,
  bbox: Rect,
  scale: number = 1
): boolean {
  return isPointInHandle(point, getRotationHandlePosition(bbox, scale));
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
 * Check if a point is inside a bbox. For a rotated bbox the point is first
 * un-rotated into the box's local (axis-aligned) frame about its centre.
 */
export function isPointInBbox(point: Point, bbox: Rect, scale: number = 1): boolean {
  const angle = bbox.angle ?? 0;
  const local = angle
    ? rotatePointAbout(point, bboxCenter(bbox, scale), -angle)
    : point;

  const sx = bbox.x * scale;
  const sy = bbox.y * scale;
  const sw = bbox.width * scale;
  const sh = bbox.height * scale;

  return (
    local.x >= sx &&
    local.x <= sx + sw &&
    local.y >= sy &&
    local.y <= sy + sh
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
 * Half-extents of the axis-aligned box that CONTAINS a rect once rotated.
 *
 * Mirrors the backend's `_rotated_corners` (crop_editor_service.py): a rotated box's
 * footprint is |w/2·cos| + |h/2·sin| per axis, which is larger than w/2 × h/2. At
 * angle 0 it reduces exactly to (w/2, h/2), so the same clamp serves both branches.
 */
export function rotatedHalfExtents(
  width: number,
  height: number,
  angle: number
): Point {
  const t = (angle * Math.PI) / 180;
  const c = Math.abs(Math.cos(t));
  const sn = Math.abs(Math.sin(t));
  return { x: (width / 2) * c + (height / 2) * sn, y: (width / 2) * sn + (height / 2) * c };
}

/**
 * Clamp a rect's CENTRE so every rotated corner stays inside the image.
 *
 * ⚠️ Clamping x to [0, imageWidth - width] -- the axis-aligned rule -- is simply the
 * wrong constraint once the box is tilted: its corners reach further than w/2. Boxes
 * that passed that clamp were rejected by the backend on save with a message the UI
 * then discarded, so dragging or resizing a rotated cell near the FOV edge failed
 * with "Failed to update cell" and no cause. When the box cannot fit at this angle
 * at all, centre it on that axis and let the backend explain why.
 */
export function clampRotatedCentre(
  cx: number,
  cy: number,
  width: number,
  height: number,
  angle: number,
  imageWidth: number,
  imageHeight: number
): Point {
  const e = rotatedHalfExtents(width, height, angle);
  const fit = (v: number, lo: number, hi: number) =>
    lo > hi ? (lo + hi) / 2 : Math.max(lo, Math.min(hi, v));
  return {
    x: fit(cx, e.x, imageWidth - e.x),
    y: fit(cy, e.y, imageHeight - e.y),
  };
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
  const angle = original.angle ?? 0;

  // Rotated: resize in the box's LOCAL frame (anchor = opposite corner/edge), then
  // map the resulting centre shift back to image space. The SIZING arithmetic
  // reduces to the axis path at angle 0; the bounds handling does not, so the
  // rotated footprint is clamped explicitly via clampRotatedCentre.
  if (angle) {
    const localDelta = rotateVec(delta.x, delta.y, -angle);
    const dlx = localDelta.x / scale;
    const dly = localDelta.y / scale;
    const hx = handle.includes("e") ? 1 : handle.includes("w") ? -1 : 0;
    const hy = handle.includes("s") ? 1 : handle.includes("n") ? -1 : 0;

    const newW = Math.max(MIN_BBOX_SIZE, original.width + hx * dlx);
    const newH = Math.max(MIN_BBOX_SIZE, original.height + hy * dly);
    // Centre moves toward the dragged edge by half the (clamped) size change.
    const shift = rotateVec(
      (hx * (newW - original.width)) / 2,
      (hy * (newH - original.height)) / 2,
      angle
    );
    const centre = clampRotatedCentre(
      original.x + original.width / 2 + shift.x,
      original.y + original.height / 2 + shift.y,
      newW, newH, angle, imageWidth, imageHeight
    );
    return {
      x: Math.round(centre.x - newW / 2),
      y: Math.round(centre.y - newH / 2),
      width: Math.round(newW),
      height: Math.round(newH),
      angle,
    };
  }

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

  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(width),
    height: Math.round(height),
    angle: 0,
  };
}

/**
 * Calculate new bbox position after moving.
 *
 * Clamps the ROTATED footprint (clampRotatedCentre), not the axis-aligned one: a
 * tilted box reaches further than w/2, so the old axis clamp let its corners leave
 * the image and the save then failed server-side. At angle 0 the two are identical.
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

  // Clamp the rotated footprint, not the axis-aligned one. At angle 0 this is
  // exactly the old clamp (rotatedHalfExtents reduces to w/2, h/2).
  const centre = clampRotatedCentre(
    original.x + original.width / 2 + dx,
    original.y + original.height / 2 + dy,
    original.width, original.height, original.angle, imageWidth, imageHeight
  );

  return {
    x: Math.round(centre.x - original.width / 2),
    y: Math.round(centre.y - original.height / 2),
    width: original.width,
    height: original.height,
    angle: original.angle,
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
    angle: 0,
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
