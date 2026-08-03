/**
 * The projections' second visual channel: what kind of sample a point is.
 *
 * Colour already carries the protein (or the microscope, or the experiment —
 * whatever colour-by is set to), so the PTM regime cannot use hue without taking
 * that channel away from something else. It uses the marker instead: a control
 * keeps its colour but is drawn as a translucent ring, and a modified sample
 * gets a black centre dot. Everything else looks exactly as it does today.
 *
 * Kept as plain functions, away from React and recharts, because the failure
 * mode here is a plot that quietly disagrees with the data rather than one that
 * throws — see e2e/unit/pointMarker.spec.ts.
 */
import type { PTMDetailed } from "@/lib/api";
import type { ExperimentMeta } from "./umapFacets";

/**
 * What a row in the PTM vocabulary is. Mirrors `models.ptm.PTMKind` exactly.
 *
 * ⚠️ This is the *backend vocabulary* and nothing else. It is the allow-list
 * `ptmKindOf` validates against and the option set the PTM editor offers, so
 * adding a display-only member here would offer the lab a value the API rejects.
 * Legend order and what a *point* can be are separate lists below.
 */
export type PtmKind = "modification" | "control" | "none";

export const PTM_KINDS: readonly PtmKind[] = ["modification", "control", "none"];

/**
 * What a *point* on the plot is — the vocabulary plus one thing a PTM row can
 * never be: an experiment nobody has classified yet.
 *
 * ⚠️ `unrecorded` draws identically to `none`; the plot must not grow a fourth
 * symbol. It exists so the two are not *counted and labelled* together, because
 * "the lattice carried no modification" is a result and "nobody recorded a PTM"
 * is an absence, and calling the second one "Non-PTM" asserts something no row
 * in the database supports. Every experiment carries a PTM today, but
 * `ExperimentCreate.ptm_id` is optional and until 2026-07-28 all 46 of them were
 * NULL — this goes live on the next upload that skips the field.
 */
export type SampleClass = PtmKind | "unrecorded";

/**
 * Legend order: the markers the plot already drew come first, then the two that
 * are new to the reader.
 */
export const MARKER_CLASSES: readonly SampleClass[] = [
  "none",
  "unrecorded",
  "modification",
  "control",
];

/**
 * Normalise a PTM row's `kind` into one this build can draw.
 *
 * An unrecognised value becomes `none`, the marker the plot drew before this
 * feature existed. Failing the other way would relabel points as controls, which
 * is far worse than showing nothing new — but it is still a disagreement between
 * this build and the backend about the vocabulary, so it is logged rather than
 * absorbed. An absent value is the ordinary case and stays quiet.
 */
export function ptmKindOf(kind: string | null | undefined): PtmKind {
  if (PTM_KINDS.includes(kind as PtmKind)) return kind as PtmKind;
  if (kind) {
    console.error(
      `[pointMarker] unknown PTM kind ${JSON.stringify(kind)} — drawing it as a ` +
        `plain non-PTM point. Is this build older than the backend?`
    );
  }
  return "none";
}

/**
 * The class of a point, resolved from the two things the plot already holds.
 *
 * Points carry only `experiment_id`; the PTM id comes from the facet summary and
 * its kind from the reference list. Extracted out of the component and given raw
 * inputs on purpose: this composition is where the whole feature can become a
 * no-op, and in the component it was unreachable by any test — replacing it with
 * `() => "none"` left 95 unit tests and `tsc` green, and hid its own evidence,
 * because with one class present the legend removes itself too.
 *
 * `onUnresolved` fires when the experiment names a PTM this build has never
 * seen. That is not a missing assignment: it is a row a colleague created after
 * this tab cached the reference list, and drawing it plain claims "not a PTM",
 * which is a claim we cannot make.
 */
export function sampleClassOf(
  experimentId: number,
  experimentMeta: Map<number, ExperimentMeta>,
  ptmById: Map<number, PTMDetailed>,
  onUnresolved?: (ptmId: number) => void
): SampleClass {
  const ptmId = experimentMeta.get(experimentId)?.ptmId;
  if (!ptmId) return "unrecorded";

  const ptm = ptmById.get(ptmId);
  if (!ptm) {
    onUnresolved?.(ptmId);
    return "unrecorded";
  }
  return ptmKindOf(ptm.kind);
}

export interface MarkerStyle {
  fillOpacity: number;
  stroke: string;
  strokeWidth: number;
  /** Absent when the marker carries no centre dot. */
  dot?: { ratio: number; color: string };
}

/** The stroke every point has today: a hairline, so dense clusters stay legible. */
const PLAIN_STROKE = "rgba(255,255,255,0.3)";

/** Today's marker, which `none`/`unrecorded` keep and `modification` only adds to. */
const PLAIN: MarkerStyle = {
  fillOpacity: 0.75,
  stroke: PLAIN_STROKE,
  strokeWidth: 1,
};

export function markerStyle(cls: SampleClass, color: string): MarkerStyle {
  switch (cls) {
    case "control":
      // "Slightly transparent", as asked — plus a solid ring in the point's own
      // colour, because opacity alone disappears under overplotting: a faded
      // point sitting on three opaque ones is indistinguishable from an opaque
      // one. The ring costs no hue, so the colour channel stays untouched.
      return { ...PLAIN, fillOpacity: 0.18, stroke: color, strokeWidth: 1.4 };
    case "modification":
      return { ...PLAIN, dot: { ratio: 0.42, color: "#000000" } };
    case "none":
    case "unrecorded":
    default:
      return { ...PLAIN };
  }
}

/**
 * The radius recharts draws for a circle symbol of the given area.
 *
 * `size` is the ZAxis range value and is an AREA, not a diameter or a radius.
 * Reading it as anything else silently resizes every point on every projection.
 * The `max(size, 0)` clamp mirrors recharts' own line, so a degenerate size
 * yields 0 rather than NaN.
 */
export function pointRadius(size: number): number {
  return Math.sqrt(Math.max(size, 0) / Math.PI);
}

/** Floor for the centre dot, so it cannot vanish on a small point. */
export const MIN_DOT_RADIUS = 1.3;

/** Above this share of the point radius the dot stops reading as a dot. */
const MAX_DOT_SHARE = 0.6;

/**
 * The centre dot's radius, clamped at both ends.
 *
 * The floor keeps it visible; the ceiling keeps it from eating the point. Both
 * matter: the entry animation interpolates `size` from 0 (recharts
 * `Scatter.js`), so without the ceiling every modified sample spends the first
 * frames as a bare black dot with no colour around it — and at small ZAxis
 * ranges the dot would swallow the primary channel entirely.
 */
export function dotRadius(pointR: number, ratio: number): number {
  if (pointR <= 0) return 0;
  return Math.min(Math.max(pointR * ratio, MIN_DOT_RADIUS), pointR * MAX_DOT_SHARE);
}

/**
 * Count the points of each class, for the legend.
 *
 * Deliberately takes the same rows the markers are drawn from, so the key under
 * the plot cannot describe a distinction the plot did not draw.
 */
export function classCounts(
  experimentIds: number[],
  experimentMeta: Map<number, ExperimentMeta>,
  ptmById: Map<number, PTMDetailed>
): Map<SampleClass, number> {
  const counts = new Map<SampleClass, number>();
  for (const id of experimentIds) {
    const cls = sampleClassOf(id, experimentMeta, ptmById);
    counts.set(cls, (counts.get(cls) ?? 0) + 1);
  }
  return counts;
}

/**
 * Whether the marker key is worth showing.
 *
 * ⚠️ NOT "more than one class present". Filtering the plot to Detyrosination
 * alone leaves every point carrying a black centre dot and — under the old rule
 * — no key at all; filtering to Control leaves a plot of faded rings, which is
 * the universal UI language for "de-emphasised", with nothing saying otherwise.
 * The question is whether anything is drawn differently from the default, not
 * how many classes there are.
 */
export function shouldShowMarkerLegend(counts: Map<SampleClass, number>): boolean {
  return (counts.get("modification") ?? 0) > 0 || (counts.get("control") ?? 0) > 0;
}
