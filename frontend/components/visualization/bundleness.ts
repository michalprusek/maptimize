/**
 * Reading the backend's bundleness summary: how a mean is printed, and which
 * legend entry a per-class mean belongs to.
 *
 * Pure functions, because every mistake here is a silently wrong number rather
 * than an exception — a mean painted next to the wrong class, or a mean over a
 * tenth of the plot printed as if it covered all of it. Tests:
 * `e2e/unit/bundleness.spec.ts`.
 */
import type {
  Bundleness,
  BundlenessClass,
  LabelAxis,
} from "@/lib/api";

/** U+2212, not the ASCII hyphen: it is as wide as "+", so columns align. */
export const MINUS_SIGN = "\u2212";

/**
 * A z-score with an explicit sign: `+0.42`, `−0.42`.
 *
 * The sign is always printed because the scale is centred on zero and the sign
 * IS the reading (above or below the average annotated crop). Anything that
 * rounds to zero prints as `+0.00` — never `−0.00`, which would claim a
 * direction the digits do not show.
 *
 * Returns null for a value that is not a finite number. recharts and JSON both
 * deliver `null` where a number was expected, and `null.toFixed` aside,
 * `Number(null)` is 0 — which would print as a perfectly plausible "+0.00".
 */
export function formatSigned(value: unknown, decimals = 2): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const digits = Math.abs(value).toFixed(decimals);
  const isZero = Number(digits) === 0;
  return `${value < 0 && !isZero ? MINUS_SIGN : "+"}${digits}`;
}

/**
 * How many points the mean rests on: `312`, or `312/340` when some of the
 * selection has no score — so a mean over part of the plot cannot pass as a
 * mean over all of it.
 */
export function formatScoredCount(nScored: number, nPoints: number): string {
  return nScored < nPoints ? `${nScored}/${nPoints}` : `${nScored}`;
}

/** What the bundleness badge has to say about the view on screen. */
export type BundlenessBadgeForm = "hidden" | "unscored" | "scored";

/**
 * Hidden only when there is nothing plotted. Points without any score still get
 * a badge: an empty slot reads as "loading" or "broken", while "no scored
 * crops" is a fact about the selection.
 */
export function bundlenessBadgeForm(
  pointCount: number,
  bundleness: Bundleness | null | undefined
): BundlenessBadgeForm {
  if (!(pointCount > 0)) return "hidden";
  return bundleness ? "scored" : "unscored";
}

/**
 * Per-class means, keyed by the class ID — or nothing at all when they are not
 * about the classes on screen.
 *
 * ⚠️ `current` and the axis check are the whole point. The plot keeps the
 * previous response while the next one loads, so right after the reader changes
 * the colour-by the legend already lists the NEW axis' classes while the
 * response still breaks the mean down by the OLD one. Protein id 3 and
 * microscope id 3 are different things with the same key; without this guard
 * the mean of one is printed beside the other. Under custom groups the same
 * happens across a slot shift — the caller passes `current = false` until a
 * response fetched for the new arrangement is in (see `groupEpoch`).
 */
export function classMeansById(
  bundleness: Bundleness | null | undefined,
  axis: LabelAxis,
  current: boolean
): Map<number, BundlenessClass> {
  const means = new Map<number, BundlenessClass>();
  if (!bundleness || !current || bundleness.label_by !== axis) return means;
  for (const cls of bundleness.classes) {
    // A non-finite mean cannot be printed; leaving the class out shows nothing
    // beside it, which is the honest rendering of "no usable number".
    if (Number.isFinite(cls.mean)) means.set(cls.label, cls);
  }
  return means;
}

/** How one point is drawn, plus the id of the class that puts it there. */
export interface PointStyle {
  name: string;
  color: string;
  /**
   * The class on the coloured axis, as the backend keys it — or null when the
   * point has none (unassigned, in no group, in several) or it cannot be pinned.
   */
  classId: number | null;
}

export interface LegendEntry {
  name: string;
  color: string;
  count: number;
  /** See `PointStyle.classId`. Null also when points under one name disagree. */
  id: number | null;
}

/**
 * Collapse per-point styles into legend entries, most points first.
 *
 * Entries are still one per NAME, as the legend has always been. The id rides
 * along so a per-class number can be attached by id; if two different ids land
 * under one name (two experiments called the same thing), the entry's id is
 * dropped — one swatch cannot honestly carry either class' mean.
 */
export function legendEntries(styles: PointStyle[]): LegendEntry[] {
  const entries = new Map<string, LegendEntry & { conflict: boolean }>();
  for (const { name, color, classId } of styles) {
    let entry = entries.get(name);
    if (!entry) {
      entries.set(name, (entry = { name, color, count: 0, id: null, conflict: false }));
    }
    entry.count++;
    if (classId == null || entry.conflict) continue;
    if (entry.id === null) entry.id = classId;
    else if (entry.id !== classId) {
      entry.id = null;
      entry.conflict = true;
    }
  }
  return Array.from(entries.values())
    .map(({ conflict: _conflict, ...entry }) => entry)
    .sort((a, b) => b.count - a.count);
}
