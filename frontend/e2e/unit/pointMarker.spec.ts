import { expect, test } from "@playwright/test";

import {
  MARKER_KINDS,
  markerStyle,
  pointRadius,
  ptmKindOf,
  type PtmKind,
} from "../../components/visualization/pointMarker";

/**
 * The second visual channel on the projections.
 *
 * Colour says which protein a point is; this says whether the sample carried a
 * PTM, was the paired inactive-enzyme control, or had no PTM at all. Getting it
 * wrong produces a plot that is quietly misleading rather than one that errors —
 * a control drawn as a sample is exactly the comparison the lab is looking at.
 */

const COLOR = "#ef4444";

test("a recognised kind passes through untouched", () => {
  for (const kind of ["modification", "control", "none"] as PtmKind[]) {
    expect(ptmKindOf(kind)).toBe(kind);
  }
});

test("anything unrecognised falls back to the plain marker", () => {
  // Failing the other way would relabel every point as a control the moment a
  // reference list failed to load, an experiment had no PTM, or the backend
  // grew a fourth kind.
  for (const bad of [null, undefined, "", "Control", "ptm", "modification "]) {
    expect(ptmKindOf(bad)).toBe("none");
  }
});

test("only a modification gets the centre dot", () => {
  expect(markerStyle("modification", COLOR).dotRatio).toBeGreaterThan(0);
  expect(markerStyle("control", COLOR).dotRatio).toBe(0);
  expect(markerStyle("none", COLOR).dotRatio).toBe(0);
});

test("the centre dot is black, so it reads on every protein colour", () => {
  expect(markerStyle("modification", COLOR).dotColor).toBe("#000000");
});

test("only a control loses its fill", () => {
  const control = markerStyle("control", COLOR);
  const plain = markerStyle("none", COLOR);
  expect(control.fillOpacity).toBeLessThan(plain.fillOpacity);
  expect(markerStyle("modification", COLOR).fillOpacity).toBe(plain.fillOpacity);
});

test("a control is ringed in its own colour", () => {
  // Transparency alone is a weak channel under overplotting: a faded point on
  // top of three opaque ones looks opaque. The ring is what makes the class
  // readable in a dense cluster, and it has to be the point's own colour so the
  // distinction costs no hue.
  expect(markerStyle("control", COLOR).stroke).toBe(COLOR);
  expect(markerStyle("none", COLOR).stroke).not.toBe(COLOR);
  expect(markerStyle("modification", COLOR).stroke).not.toBe(COLOR);
});

test("a PTM point is otherwise identical to a plain one", () => {
  // The dot is added to today's marker, not substituted for it: every plot that
  // exists must keep looking the way it does.
  const { dotRatio: _a, dotColor: _b, ...ptm } = markerStyle("modification", COLOR);
  const { dotRatio: _c, dotColor: _d, ...plain } = markerStyle("none", COLOR);
  expect(ptm).toEqual(plain);
});

test("the radius matches the circle recharts draws for the same area", () => {
  // recharts sizes symbols by AREA (the ZAxis range), not by diameter. Reading
  // `size` as anything else silently resizes every point on every projection.
  expect(pointRadius(60)).toBeCloseTo(4.3702, 3);
  expect(Math.PI * pointRadius(60) ** 2).toBeCloseTo(60, 6);
});

test("the legend lists every kind exactly once, plain marker first", () => {
  expect(MARKER_KINDS).toEqual(["none", "modification", "control"]);
});
