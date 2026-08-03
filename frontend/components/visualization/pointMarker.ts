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

/** Mirrors `models.ptm.PTMKind`. One vocabulary, not two. */
export type PtmKind = "modification" | "control" | "none";

/**
 * Legend order: the marker the plot already draws comes first, then the two
 * that are new to the reader.
 */
export const MARKER_KINDS: readonly PtmKind[] = ["none", "modification", "control"];

/**
 * Normalise whatever the API gave us into a kind we can draw.
 *
 * Everything unrecognised — an experiment with no PTM, a reference list that
 * failed to load, a value some future backend added — becomes `none`, the marker
 * the plot draws today. Failing the other way would relabel every point as a
 * control, which is far worse than showing nothing new.
 */
export function ptmKindOf(kind: string | null | undefined): PtmKind {
  return MARKER_KINDS.includes(kind as PtmKind) ? (kind as PtmKind) : "none";
}

export interface MarkerStyle {
  fillOpacity: number;
  stroke: string;
  strokeWidth: number;
  /** Centre-dot radius as a fraction of the point radius. 0 means no dot. */
  dotRatio: number;
  dotColor: string;
}

/** The stroke every point has today: a hairline, so dense clusters stay legible. */
const PLAIN_STROKE = "rgba(255,255,255,0.3)";

/** Today's marker, which `none` keeps unchanged and `modification` only adds to. */
const PLAIN: MarkerStyle = {
  fillOpacity: 0.75,
  stroke: PLAIN_STROKE,
  strokeWidth: 1,
  dotRatio: 0,
  dotColor: "",
};

export function markerStyle(kind: PtmKind, color: string): MarkerStyle {
  switch (kind) {
    case "control":
      // "Slightly transparent", as asked — plus a solid ring in the point's own
      // colour, because opacity alone disappears under overplotting: a faded
      // point sitting on three opaque ones is indistinguishable from an opaque
      // one. The ring costs no hue, so the colour channel stays untouched.
      return { ...PLAIN, fillOpacity: 0.18, stroke: color, strokeWidth: 1.4 };
    case "modification":
      return { ...PLAIN, dotRatio: 0.42, dotColor: "#000000" };
    case "none":
    default:
      return { ...PLAIN };
  }
}

/**
 * The radius recharts draws for a circle symbol of the given area.
 *
 * `size` is the ZAxis range value and is an AREA, not a diameter or a radius.
 * Reading it as anything else silently resizes every point on every projection.
 */
export function pointRadius(size: number): number {
  return Math.sqrt(size / Math.PI);
}

/** Floor for the centre dot, so it cannot vanish on a small point. */
export const MIN_DOT_RADIUS = 1.3;
