import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import { withGeometryFrom } from "../../lib/editor/types";
import type { EditorBbox, Rect } from "../../lib/editor/types";

/**
 * The interaction hooks write every intermediate value onto the live bbox during a
 * drag, so when a drag completes the bbox already holds the NEW geometry. The undo
 * snapshot must therefore take every geometry field from the pre-drag rect.
 *
 * Regression: `angle` was the one field not overridden, so undoing a rotation sent
 * the new angle back to the API, re-cut the crop for nothing and popped the action
 * off the stack -- the rotation was unrecoverable and the box visibly did not move.
 * tsc cannot catch it: `angle` is required on EditorBbox, so the spread type-checks
 * whether or not the field is overridden.
 */

/** A bbox mid-rotation: 44 degrees applied, origin unchanged. */
const liveBbox: EditorBbox = {
  id: 7521,
  x: 400,
  y: 356,
  width: 620,
  height: 401,
  angle: 44,
  cropId: 7521,
  isNew: false,
  isModified: true,
};

/** What it looked like at mousedown. */
const original: Rect = { x: 400, y: 356, width: 620, height: 401, angle: 0 };

test("undoing a rotation restores the ORIGINAL angle, not the new one", () => {
  expect(withGeometryFrom(liveBbox, original).angle).toBe(0);
});

test("every geometry field comes from the pre-drag rect", () => {
  const moved: Rect = { x: 10, y: 20, width: 30, height: 40, angle: -15 };
  const out = withGeometryFrom({ ...liveBbox, x: 999, y: 999, width: 1, height: 2 }, moved);
  expect({
    x: out.x,
    y: out.y,
    width: out.width,
    height: out.height,
    angle: out.angle,
  }).toEqual(moved);
});

test("non-geometry fields are kept from the live bbox", () => {
  // cropId is what the undo handler needs to address the API; losing it would
  // make undo a silent no-op rather than a wrong write.
  const out = withGeometryFrom(liveBbox, original);
  expect(out.cropId).toBe(7521);
  expect(out.id).toBe(7521);
});

test("a pre-drag rect with no angle reads as axis-aligned", () => {
  const noAngle = { x: 400, y: 356, width: 620, height: 401 } as Rect;
  expect(withGeometryFrom(liveBbox, noAngle).angle).toBe(0);
});

test("both editor call sites go through the helper, not a hand-rolled spread", () => {
  // A pure test of withGeometryFrom cannot catch a call site that never calls it --
  // and that is exactly how this bug shipped twice, in the undo snapshot and in
  // "reset to original", each spreading the live bbox and overriding four of the
  // five geometry fields. So pin the SHAPE of the code, the way
  // test_writes_rebuild_the_response_by_reselecting_the_row does on the backend.
  const src = readFileSync(
    join(__dirname, "../../components/editor/ImageEditorPage.tsx"),
    "utf8"
  );
  const calls = src.match(/withGeometryFrom\(/g) ?? [];
  expect(calls.length, "undo snapshot and reset must both use the helper").toBe(2);

  // No hand-rolled geometry restore may come back.
  for (const banned of [
    /width:\s*originalBbox\.width/,
    /width:\s*bbox\.original\.width/,
  ]) {
    expect(src, `hand-rolled geometry restore reintroduced: ${banned}`).not.toMatch(
      banned
    );
  }
});

test("a rotation-only edit is still a real change to undo", () => {
  // Guards the case that motivated the bug: nothing but the angle differs, so a
  // snapshot that copied the angle from the live bbox would equal the new state
  // and undo would have nothing to send.
  const before = withGeometryFrom(liveBbox, original);
  expect(before.angle).not.toBe(liveBbox.angle);
});
