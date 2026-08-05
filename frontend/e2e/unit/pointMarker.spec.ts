import { expect, test } from "@playwright/test";

import {
  MARKER_CLASSES,
  MIN_DOT_RADIUS,
  PTM_KINDS,
  classCounts,
  dotRadius,
  markerStyle,
  pointRadius,
  ptmKindOf,
  sampleClassOf,
  shouldShowMarkerLegend,
  type PtmKind,
  type SampleClass,
} from "../../components/visualization/pointMarker";
import { experimentMetaById } from "../../components/visualization/umapFacets";
import type { PTMDetailed, UmapFacetRow } from "../../lib/api";

/**
 * The second visual channel on the projections.
 *
 * Colour says which protein a point is; this says whether the sample carried a
 * PTM, was the paired inactive-enzyme control, or had no PTM at all. Getting it
 * wrong produces a plot that is quietly misleading rather than one that errors —
 * a control drawn as a sample is exactly the comparison the lab is looking at.
 */

const COLOR = "#ef4444";

// -- the vocabulary ---------------------------------------------------------

test("a recognised kind passes through untouched", () => {
  for (const kind of PTM_KINDS) {
    expect(ptmKindOf(kind)).toBe(kind);
  }
});

test("anything unrecognised falls back to the plain marker", () => {
  // Failing the other way would relabel points as controls the moment a
  // reference list failed to load or the backend grew a fourth kind.
  for (const bad of [null, undefined, "", "Control", "ptm", "modification "]) {
    expect(ptmKindOf(bad)).toBe("none");
  }
});

test("the editor's option list holds only values the API accepts", () => {
  // PTM_KINDS drives both the <select> on /dashboard/ptms and the allow-list
  // above. MARKER_CLASSES carries one extra, display-only member; offering that
  // one in the editor would hand the lab a value the backend rejects with 422.
  expect([...PTM_KINDS].sort()).toEqual(["control", "modification", "none"]);
  expect(PTM_KINDS).not.toContain("unrecorded");
});

test("the legend lists every class exactly once, plain markers first", () => {
  expect(MARKER_CLASSES).toEqual(["none", "unrecorded", "modification", "control"]);
});

// -- resolving a point's class ----------------------------------------------

function facetRow(over: Partial<UmapFacetRow> = {}): UmapFacetRow {
  return {
    experiment_id: 1,
    experiment_name: "E1",
    microscope_id: null,
    ptm_id: null,
    protein_id: null,
    count: 1,
    ...over,
  };
}

function ptm(id: number, kind: string): PTMDetailed {
  return { id, name: `PTM${id}`, kind, experiment_count: 0 };
}

/** The real chain the component uses: facet rows -> meta -> reference list. */
function resolve(rows: UmapFacetRow[], ptms: PTMDetailed[], experimentId: number) {
  return sampleClassOf(
    experimentId,
    experimentMetaById(rows),
    new Map(ptms.map((p) => [p.id, p]))
  );
}

test("a control experiment resolves to control, through the real facet chain", () => {
  // The whole feature can become a no-op here and nothing else notices: with
  // every point "none" the legend removes itself, so the plot is byte-identical
  // to before the feature existed. Driven from raw API shapes on purpose — a
  // facet row losing ptm_id, or the PTM list losing kind, must redden this.
  const rows = [facetRow({ experiment_id: 7, ptm_id: 15 })];
  expect(resolve(rows, [ptm(15, "control")], 7)).toBe("control");
});

test("a modified experiment resolves to modification", () => {
  const rows = [facetRow({ experiment_id: 7, ptm_id: 2 })];
  expect(resolve(rows, [ptm(2, "modification")], 7)).toBe("modification");
});

test("an experiment recorded as unmodified is not the same as one nobody classified", () => {
  // Both draw identically, but "the lattice carried no modification" is a
  // result and "nobody recorded a PTM" is an absence. Counting them together
  // and calling it Non-PTM asserts something no row supports.
  const recorded = [facetRow({ experiment_id: 7, ptm_id: 10 })];
  expect(resolve(recorded, [ptm(10, "none")], 7)).toBe("none");

  const unassigned = [facetRow({ experiment_id: 7, ptm_id: null })];
  expect(resolve(unassigned, [], 7)).toBe("unrecorded");
});

test("a PTM id this build has never seen is reported, not silently drawn plain", () => {
  // A colleague created the row after this tab cached the reference list. The
  // ptms query effectively never refetches on a dashboard that mounts once, so
  // without this the points stay wrong for the life of the tab.
  const seen: number[] = [];
  const cls = sampleClassOf(
    7,
    experimentMetaById([facetRow({ experiment_id: 7, ptm_id: 99 })]),
    new Map(),
    (id) => seen.push(id)
  );
  expect(cls).toBe("unrecorded");
  expect(seen).toEqual([99]);
});

test("an experiment with no facet row at all does not throw", () => {
  expect(resolve([], [], 404)).toBe("unrecorded");
});

// -- marker styling ---------------------------------------------------------

test("only a modification gets the centre dot", () => {
  expect(markerStyle("modification", COLOR).dot).toBeDefined();
  expect(markerStyle("control", COLOR).dot).toBeUndefined();
  expect(markerStyle("none", COLOR).dot).toBeUndefined();
  expect(markerStyle("unrecorded", COLOR).dot).toBeUndefined();
});

test("the centre dot is black, so it reads on every protein colour", () => {
  expect(markerStyle("modification", COLOR).dot?.color).toBe("#000000");
});

test("only a control loses its fill", () => {
  const control = markerStyle("control", COLOR);
  const plain = markerStyle("none", COLOR);
  expect(control.fillOpacity).toBeLessThan(plain.fillOpacity);
  expect(markerStyle("modification", COLOR).fillOpacity).toBe(plain.fillOpacity);
});

test("a control is ringed in its own colour", () => {
  // Transparency alone is a weak channel under overplotting: a faded point on
  // top of three opaque ones looks opaque. The ring makes the class readable in
  // a dense cluster, and it must be the point's own colour so it costs no hue.
  expect(markerStyle("control", COLOR).stroke).toBe(COLOR);
  expect(markerStyle("none", COLOR).stroke).not.toBe(COLOR);
  expect(markerStyle("modification", COLOR).stroke).not.toBe(COLOR);
});

test("a PTM point is otherwise identical to a plain one", () => {
  // The dot is added to today's marker, not substituted for it: every plot that
  // exists must keep looking the way it does.
  const { dot: _d, ...ptmStyle } = markerStyle("modification", COLOR);
  expect(ptmStyle).toEqual(markerStyle("none", COLOR));
});

test("an unrecorded sample is drawn exactly like an unmodified one", () => {
  // The distinction is for counting and labelling, never a fourth symbol.
  expect(markerStyle("unrecorded", COLOR)).toEqual(markerStyle("none", COLOR));
});

// -- geometry ---------------------------------------------------------------

test("the radius matches the circle recharts draws for the same area", () => {
  // recharts sizes symbols by AREA (the ZAxis range), not by diameter. Reading
  // `size` as anything else silently resizes every point on every projection.
  expect(pointRadius(60)).toBeCloseTo(4.3702, 3);
  expect(Math.PI * pointRadius(60) ** 2).toBeCloseTo(60, 6);
});

test("a degenerate size yields 0, not NaN — same as recharts' own clamp", () => {
  expect(pointRadius(0)).toBe(0);
  expect(pointRadius(-1)).toBe(0);
});

test("the centre dot never outgrows the point it sits in", () => {
  // The entry animation interpolates size from 0, so without a ceiling a
  // modified sample spends its first frames as a bare black dot with no colour
  // around it — and a smaller ZAxis range would let the dot eat the point
  // entirely, destroying the primary channel.
  expect(dotRadius(0, 0.42)).toBe(0);
  for (const r of [0.5, 1, 2, 4.3702, 20]) {
    expect(dotRadius(r, 0.42)).toBeLessThanOrEqual(r);
  }
});

test("the centre dot stays visible on an ordinary point", () => {
  expect(dotRadius(4.3702, 0.42)).toBeGreaterThanOrEqual(MIN_DOT_RADIUS);
});

// -- counting and the legend ------------------------------------------------

test("classes are counted per point, not per experiment", () => {
  const rows = [
    facetRow({ experiment_id: 1, ptm_id: 15 }),
    facetRow({ experiment_id: 2, ptm_id: 2 }),
  ];
  const counts = classCounts(
    [1, 1, 1, 2],
    experimentMetaById(rows),
    new Map([
      [15, ptm(15, "control")],
      [2, ptm(2, "modification")],
    ])
  );
  expect(counts.get("control")).toBe(3);
  expect(counts.get("modification")).toBe(1);
});

function counts(entries: Array<[SampleClass, number]>): Map<SampleClass, number> {
  return new Map(entries);
}

test("the key appears whenever anything is drawn differently", () => {
  // ⚠️ NOT "more than one class present". Filtering the plot to one PTM leaves
  // every point wearing a black centre dot; filtering to controls leaves a plot
  // of faded rings, which reads as "de-emphasised" to anyone who was not told.
  expect(shouldShowMarkerLegend(counts([["modification", 143]]))).toBe(true);
  expect(shouldShowMarkerLegend(counts([["control", 48]]))).toBe(true);
});

test("the key stays hidden when every point is the default marker", () => {
  // A lab that has recorded no PTM must not be handed a legend explaining a
  // distinction its plot does not make.
  expect(shouldShowMarkerLegend(counts([["none", 1350]]))).toBe(false);
  expect(
    shouldShowMarkerLegend(counts([["none", 100], ["unrecorded", 200]]))
  ).toBe(false);
});

test("every class the legend can order has a style", () => {
  // Guards the two lists drifting: a class in MARKER_CLASSES with no branch in
  // markerStyle would fall through to the default and draw as plain.
  for (const cls of MARKER_CLASSES) {
    expect(markerStyle(cls as PtmKind | "unrecorded", COLOR)).toBeTruthy();
  }
});
