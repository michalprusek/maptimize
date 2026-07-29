import { expect, test } from "@playwright/test";

import {
  bboxCenter,
  clampRotatedCentre,
  getRotationHandlePosition,
  isPointInBbox,
  isPointInRotationHandle,
  moveBbox,
  resizeBbox,
  rotateVec,
  rotatedHalfExtents,
} from "../../lib/editor/geometry";
import type { EditorBbox, Rect } from "../../lib/editor/types";

/**
 * The rotation-aware geometry is pure, deterministic and was entirely untested: a
 * sign error or a wrong anchor shows up as a box that drifts under the cursor, not
 * as an exception. These pin the properties the maths is supposed to have, rather
 * than the numbers it happens to produce.
 */

/** Corner of a rotated rect in image space, sx/sy pick which one. */
function corner(b: Rect, sx: -1 | 1, sy: -1 | 1) {
  const c = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  const v = rotateVec((sx * b.width) / 2, (sy * b.height) / 2, b.angle);
  return { x: c.x + v.x, y: c.y + v.y };
}

const corners = (b: Rect) =>
  [corner(b, -1, -1), corner(b, 1, -1), corner(b, -1, 1), corner(b, 1, 1)];

// --- rotatedHalfExtents -------------------------------------------------------

test("at angle 0 the rotated footprint is just the box", () => {
  // Load-bearing: moveBbox now uses this for BOTH branches, so if it did not
  // reduce exactly, every unrotated drag would start clamping differently.
  expect(rotatedHalfExtents(80, 60, 0)).toEqual({ x: 40, y: 30 });
});

test("turning a box always widens its footprint", () => {
  const flat = rotatedHalfExtents(80, 60, 0);
  for (const angle of [15, 30, 45, -37, 90]) {
    const e = rotatedHalfExtents(80, 60, angle);
    expect(e.x + e.y).toBeGreaterThanOrEqual(flat.x + flat.y - 1e-9);
  }
  // A square at 45 degrees spans its diagonal.
  const sq = rotatedHalfExtents(40, 40, 45);
  expect(sq.x).toBeCloseTo((40 * Math.SQRT2) / 2, 6);
});

test("the footprint is symmetric in the sign of the angle", () => {
  expect(rotatedHalfExtents(80, 60, 33)).toEqual(rotatedHalfExtents(80, 60, -33));
});

// --- clampRotatedCentre -------------------------------------------------------

test("a clamped centre keeps every rotated corner inside the image", () => {
  const W = 200;
  const H = 200;
  for (const angle of [0, 20, 45, -60, 90]) {
    const c = clampRotatedCentre(500, -100, 60, 40, angle, W, H); // wildly outside
    const box: Rect = { x: c.x - 30, y: c.y - 20, width: 60, height: 40, angle };
    for (const p of corners(box)) {
      expect(p.x).toBeGreaterThanOrEqual(-1e-6);
      expect(p.y).toBeGreaterThanOrEqual(-1e-6);
      expect(p.x).toBeLessThanOrEqual(W + 1e-6);
      expect(p.y).toBeLessThanOrEqual(H + 1e-6);
    }
  }
});

test("a centre already legal is left alone", () => {
  expect(clampRotatedCentre(100, 100, 40, 40, 45, 200, 200)).toEqual({ x: 100, y: 100 });
});

test("a box too large to fit at this angle is centred rather than jammed", () => {
  // 180x180 at 45 degrees needs ~254px; in a 200px frame no position is legal, so
  // the best available answer is the middle -- and the backend then explains why.
  expect(clampRotatedCentre(10, 10, 180, 180, 45, 200, 200)).toEqual({ x: 100, y: 100 });
});

// --- moveBbox -----------------------------------------------------------------

test("a rotated box cannot be dragged out of the image", () => {
  const orig: Rect = { x: 150, y: 150, width: 40, height: 40, angle: 45 };
  const out = moveBbox(orig, { x: 999, y: 999 }, 1, 200, 200);
  for (const p of corners(out)) {
    expect(p.x).toBeLessThanOrEqual(200 + 1);
    expect(p.y).toBeLessThanOrEqual(200 + 1);
  }
  expect(out.angle).toBe(45); // the drag must not silently un-rotate it
});

test("an unrotated drag clamps exactly where it always did", () => {
  // Guards the unification: rotatedHalfExtents reduces at angle 0, so this must
  // still land flush against the edge, not one half-extent short of it.
  const orig: Rect = { x: 10, y: 10, width: 40, height: 30, angle: 0 };
  const out = moveBbox(orig, { x: 999, y: 999 }, 1, 200, 100);
  expect({ x: out.x, y: out.y }).toEqual({ x: 160, y: 70 }); // 200-40, 100-30
});

// --- resizeBbox ---------------------------------------------------------------

test("resizing a rotated box keeps the opposite corner anchored", () => {
  // The whole point of resizing in the box's local frame: dragging the SE handle
  // must not drag the NW corner along with it.
  const orig: Rect = { x: 100, y: 100, width: 80, height: 60, angle: 30 };
  const before = corner(orig, -1, -1);
  const out = resizeBbox(orig, "se", rotateVec(20, 10, 30), 1, 10000, 10000);
  const after = corner(out, -1, -1);
  expect(Math.abs(after.x - before.x)).toBeLessThan(1.5);
  expect(Math.abs(after.y - before.y)).toBeLessThan(1.5);
  expect(out.width).toBeCloseTo(100, 0);
  expect(out.height).toBeCloseTo(70, 0);
  expect(out.angle).toBe(30);
});

test("resizing a rotated box from the NW handle anchors the SE corner", () => {
  const orig: Rect = { x: 100, y: 100, width: 80, height: 60, angle: 30 };
  const before = corner(orig, 1, 1);
  const out = resizeBbox(orig, "nw", rotateVec(-20, -10, 30), 1, 10000, 10000);
  const after = corner(out, 1, 1);
  expect(Math.abs(after.x - before.x)).toBeLessThan(1.5);
  expect(Math.abs(after.y - before.y)).toBeLessThan(1.5);
});

test("an axis-aligned resize reports angle 0 rather than undefined", () => {
  // Rect.angle is required; a producer omitting it used to type-check and write
  // undefined into the field.
  const out = resizeBbox(
    { x: 10, y: 10, width: 50, height: 50, angle: 0 },
    "se",
    { x: 10, y: 10 },
    1,
    500,
    500
  );
  expect(out.angle).toBe(0);
});

// --- hit-testing --------------------------------------------------------------

const rotated: EditorBbox = {
  id: 1,
  x: 100,
  y: 100,
  width: 100,
  height: 20,
  angle: 90,
};

test("hit-testing follows the rotation, not the axis-aligned rect", () => {
  // Turned 90 degrees, a wide flat box becomes tall and narrow about the same
  // centre: points that were inside are now outside and vice versa.
  const centre = bboxCenter(rotated);
  expect(isPointInBbox(centre, rotated)).toBe(true);
  expect(isPointInBbox({ x: centre.x, y: centre.y + 40 }, rotated)).toBe(true);
  expect(isPointInBbox({ x: centre.x + 40, y: centre.y }, rotated)).toBe(false);
});

test("the rotation handle is hit-testable exactly where it is drawn", () => {
  for (const angle of [0, 37, -90]) {
    const b = { ...rotated, angle };
    const p = getRotationHandlePosition(b);
    expect(isPointInRotationHandle(p, b, 1)).toBe(true);
    expect(isPointInRotationHandle({ x: p.x + 60, y: p.y + 60 }, b, 1)).toBe(false);
  }
});
