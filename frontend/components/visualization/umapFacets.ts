/**
 * Turning the backend's facet summary into filter options.
 *
 * The UMAP response summarises the plot once per (experiment, protein) bucket
 * rather than repeating an experiment's microscope and PTM on each of its
 * hundreds of points. Everything the filter panel and the colour-by selector
 * need is derived from that summary here, so the two cannot disagree about how
 * many points a value has.
 */
import type {
  LabelAxis,
  UmapFacetRow,
  UmapFacetSelection,
  UnscoredReason,
} from "@/lib/api";

/** Reserved id for "this facet is not assigned". Mirrors UNASSIGNED_FACET_ID. */
export const UNASSIGNED_ID = 0;

/**
 * The dimensions the plot can be filtered and coloured by.
 *
 * Aliased to the request type rather than re-declared: this used to be a third,
 * independent copy, and structural typing meant adding a facet here alone still
 * compiled — the pill showed ticked and the filter never reached the wire.
 */
export type FacetSelection = UmapFacetSelection;
export type FacetKey = keyof UmapFacetSelection;

// Frozen down to the arrays, because it is exported and shared: a shallow freeze
// blocks only `EMPTY_SELECTION.ptm = [...]`, while the realistic corruption is
// `EMPTY_SELECTION.ptm.push(...)` on the value every consumer starts from.
export const EMPTY_SELECTION: FacetSelection = Object.freeze({
  experiment: Object.freeze([]) as unknown as number[],
  microscope: Object.freeze([]) as unknown as number[],
  protein: Object.freeze([]) as unknown as number[],
  ptm: Object.freeze([]) as unknown as number[],
  cell_line: Object.freeze([]) as unknown as number[],
});

/**
 * The i18n key holding each facet's display name.
 *
 * One map rather than a `t("facet…")` call spelled out at each site: the filter
 * headings, the colour-by options and the separability badge must all name a
 * dimension the same way, and the badge is the one place where a wrong name
 * would misattribute a number rather than just read oddly.
 */
export const FACET_LABEL_KEY: Record<FacetKey, string> = {
  experiment: "facetExperiment",
  microscope: "facetMicroscope",
  protein: "facetProtein",
  ptm: "facetPtm",
  cell_line: "facetCellLine",
};

/**
 * The i18n key naming each thing the plot can be coloured and scored by: the
 * facets, plus the reader's own groups.
 *
 * Spread from `FACET_LABEL_KEY` so a facet is named once; the `Record` makes a
 * label axis the backend gains a compile error here instead of a badge that
 * prints `undefined`.
 */
export const LABEL_AXIS_KEY: Record<LabelAxis, string> = {
  ...FACET_LABEL_KEY,
  group: "colorByGroups",
};

/**
 * The i18n key explaining each reason a separability score is missing.
 *
 * A `Record` so a reason the backend adds is a compile error here rather than
 * a badge that renders the literal key.
 */
export const UNSCORED_REASON_KEY: Record<UnscoredReason, string> = {
  no_labels: "separabilityNoLabels",
  single_class: "separabilitySingleClass",
  too_few_points: "separabilityTooFewPoints",
  failed: "separabilityFailed",
};

/**
 * Every facet, in the order the filter panel stacks their sections.
 *
 * ⚠️ Derived from `FACET_LABEL_KEY`, never written out again. That map is a
 * `Record<FacetKey, string>`, so the type system guarantees it covers every
 * facet — and a second hand-written list is exactly how the cell-line facet
 * reached production with its options computed, its wire param sent, and no
 * pills drawn. Nothing failed: the array was cast, so tsc was satisfied, and the
 * pure-function tests never look at what the component renders.
 */
export const FACET_ORDER = Object.keys(FACET_LABEL_KEY) as FacetKey[];

/**
 * Rank of each facet in the colour-by selector. A different order from
 * `FACET_ORDER` on purpose: protein is what the plot has always been about, and
 * experiment goes last because it produces by far the most classes.
 *
 * A `Record`, not a list, for the same reason as above — a missing facet is a
 * compile error rather than a silently short dropdown.
 */
const COLOR_BY_RANK: Record<FacetKey, number> = {
  protein: 0,
  microscope: 1,
  ptm: 2,
  cell_line: 3,
  experiment: 4,
};

/**
 * The colour-by options, most useful first. The reader's own groups go last:
 * they are empty until someone builds them.
 */
export const COLOR_BY_ORDER: readonly LabelAxis[] = [
  ...(Object.keys(COLOR_BY_RANK) as FacetKey[]).sort(
    (a, b) => COLOR_BY_RANK[a] - COLOR_BY_RANK[b]
  ),
  "group",
];

/**
 * Which message explains a missing score.
 *
 * Under custom groups, "no labels" and "one class" are not facts about the data
 * — they mean the reader has not finished building the comparison, and the
 * useful thing to say is what to do next.
 */
export function unscoredMessageKey(unscored: {
  label_by: LabelAxis;
  reason: UnscoredReason;
}): string {
  if (
    unscored.label_by === "group" &&
    (unscored.reason === "no_labels" || unscored.reason === "single_class")
  ) {
    return "separabilityNeedsGroups";
  }
  return UNSCORED_REASON_KEY[unscored.reason];
}

export interface FacetOption {
  id: number;
  name: string;
  color?: string | null;
  /** Points carrying this value across the whole readable scope. */
  count: number;
}

/** A reference row with an id, a display name and a legend colour. */
export interface Named {
  id: number;
  name: string;
  color?: string | null;
}

export function isSelectionEmpty(selection: FacetSelection): boolean {
  return (Object.keys(selection) as FacetKey[]).every(
    (key) => selection[key].length === 0
  );
}

export function countActiveFilters(selection: FacetSelection): number {
  return (Object.keys(selection) as FacetKey[]).reduce(
    (total, key) => total + selection[key].length,
    0
  );
}

/** Add or remove one value from one facet, leaving the others untouched. */
export function toggleFacetValue(
  selection: FacetSelection,
  facet: FacetKey,
  id: number
): FacetSelection {
  const current = selection[facet];
  const next = current.includes(id)
    ? current.filter((existing) => existing !== id)
    : [...current, id];
  return { ...selection, [facet]: next };
}

/** Which column of a facet row this facet reads. */
function facetIdOf(row: UmapFacetRow, facet: FacetKey): number | null {
  switch (facet) {
    case "experiment":
      return row.experiment_id;
    case "microscope":
      return row.microscope_id;
    case "protein":
      return row.protein_id;
    case "ptm":
      return row.ptm_id;
    case "cell_line":
      return row.cell_line_id;
  }
}

/**
 * Narrow every facet's options by ONE search string.
 *
 * One box for the whole panel, not one per section: the reader types "mtcl1"
 * to find it wherever it lives (protein, experiment name…) instead of working
 * out which of five lists to search. A blank needle returns the input itself,
 * so "no search" costs nothing and cannot drop anything.
 */
export function searchFacetOptions<Options extends Record<FacetKey, FacetOption[]>>(
  options: Options,
  search: string
): Options {
  const needle = search.trim().toLowerCase();
  if (!needle) return options;
  const narrowed = { ...options };
  for (const facet of FACET_ORDER) {
    narrowed[facet] = options[facet].filter((option) =>
      option.name.toLowerCase().includes(needle)
    );
  }
  return narrowed;
}

/**
 * Options for one facet, ordered by point count.
 *
 * `references` supplies names and colours for the assigned values; experiments
 * carry their name on the facet row itself, so that facet passes none. A null id
 * on any row becomes the "unassigned" option, which is what makes the PTM facet
 * usable before the lab has backfilled it.
 */
export function facetOptions(
  rows: UmapFacetRow[],
  facet: FacetKey,
  references: Named[] | undefined,
  unassignedLabel: string
): FacetOption[] {
  const counts = new Map<number, number>();
  const experimentNames = new Map<number, string>();

  for (const row of rows) {
    const id = facetIdOf(row, facet) ?? UNASSIGNED_ID;
    counts.set(id, (counts.get(id) ?? 0) + row.count);
    if (facet === "experiment") {
      experimentNames.set(row.experiment_id, row.experiment_name);
    }
  }

  // Reference values with no points still belong in the list: the user has to be
  // able to tell "nothing acquired with this yet" from "this does not exist".
  const byId = new Map((references ?? []).map((ref) => [ref.id, ref]));
  for (const ref of references ?? []) {
    if (!counts.has(ref.id)) counts.set(ref.id, 0);
  }

  const options: FacetOption[] = [];
  for (const [id, count] of Array.from(counts.entries())) {
    if (id === UNASSIGNED_ID) {
      options.push({ id, name: unassignedLabel, color: null, count });
      continue;
    }
    const ref = byId.get(id);
    options.push({
      id,
      name: ref?.name ?? experimentNames.get(id) ?? `#${id}`,
      // Experiments have no reference row to take a colour from, so they use the
      // same derived hue the points do. Without this every experiment pill in the
      // panel renders the same grey while the plot draws them distinctly.
      color: ref?.color ?? (facet === "experiment" ? experimentColor(id) : null),
      count,
    });
  }

  return options.sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}

// Golden angle as a fraction of a turn, mirroring utils/colors.py: it spreads
// hues evenly so neighbouring ids do not come out near-identical.
const HUE_STEP = 0.381966;

/**
 * A stable colour for an experiment.
 *
 * Experiments have no colour column — unlike proteins, microscopes, PTMs and
 * cell lines there are too many of them to curate. Deriving it from the id rather than from
 * the value's position in the list keeps a point the same colour when the filter
 * changes what else is on the plot.
 */
export function experimentColor(id: number): string {
  return `hsl(${Math.round(((id * HUE_STEP) % 1) * 360)}, 62%, 60%)`;
}

/** Total points in the readable scope, before any facet filter. */
export function totalPoints(rows: UmapFacetRow[]): number {
  return rows.reduce((total, row) => total + row.count, 0);
}

export interface ExperimentMeta {
  name: string;
  microscopeId: number | null;
  ptmId: number | null;
  cellLineId: number | null;
}

/**
 * experiment id -> its acquisition metadata.
 *
 * Points carry only `experiment_id`; this is how colouring by microscope, PTM or
 * cell line, and the tooltip rows for them, get their value without the payload repeating
 * it per point.
 */
export function experimentMetaById(
  rows: UmapFacetRow[]
): Map<number, ExperimentMeta> {
  const meta = new Map<number, ExperimentMeta>();
  for (const row of rows) {
    if (!meta.has(row.experiment_id)) {
      meta.set(row.experiment_id, {
        name: row.experiment_name,
        microscopeId: row.microscope_id,
        ptmId: row.ptm_id,
        cellLineId: row.cell_line_id,
      });
    }
  }
  return meta;
}

/**
 * Serialise the selection for a React Query key.
 *
 * Sorted, because [3,4] and [4,3] are the same filter and must not be two cache
 * entries — and because an unsorted key would refetch every time a user ticked
 * values in a different order.
 */
export function selectionKey(selection: FacetSelection): string {
  return (Object.keys(selection) as FacetKey[])
    .sort()
    .map((facet) => `${facet}:${[...selection[facet]].sort((a, b) => a - b).join(",")}`)
    .join("|");
}

const FACET_PARAMS: Record<FacetKey, string> = {
  experiment: "experiment",
  microscope: "microscope",
  protein: "protein",
  ptm: "ptm",
  cell_line: "cell_line",
};

/** Read a selection out of a URL query string, ignoring anything malformed. */
export function selectionFromQuery(search: string): FacetSelection {
  const params = new URLSearchParams(search);
  const selection: FacetSelection = {
    experiment: [], microscope: [], protein: [], ptm: [], cell_line: [],
  };

  for (const facet of Object.keys(FACET_PARAMS) as FacetKey[]) {
    const raw = params.get(FACET_PARAMS[facet]);
    if (!raw) continue;
    // The unassigned sentinel is meaningless for experiments: that column is NOT
    // NULL, so a stray 0 there only produces a 404 the client then has to undo.
    const lowest = facet === "experiment" ? 1 : 0;
    selection[facet] = raw
      .split(",")
      // Drop blanks BEFORE Number(): `Number("")` is 0, which is the
      // "unassigned" sentinel, so a stray comma in a hand-edited or shared URL
      // would silently widen the filter instead of being ignored.
      .map((value) => value.trim())
      .filter((value) => value !== "")
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value >= lowest);
  }

  return selection;
}

/**
 * Write the selection into an existing query string, leaving other params alone.
 *
 * Takes the page's current search string rather than building from scratch: the
 * plot owns its own params, not the whole URL, and silently dropping a param some
 * other part of the page put there would be a nasty surprise for whoever adds
 * one. Empty facets are removed, not written blank.
 */
export function selectionToQuery(selection: FacetSelection, search = ""): string {
  const params = new URLSearchParams(search);
  for (const facet of Object.keys(FACET_PARAMS) as FacetKey[]) {
    const ids = selection[facet];
    if (ids.length > 0) {
      params.set(FACET_PARAMS[facet], [...ids].sort((a, b) => a - b).join(","));
    } else {
      params.delete(FACET_PARAMS[facet]);
    }
  }
  return params.toString();
}

/** Backend 404 detail prefix -> the facet whose ids it names. */
const FACET_BY_ERROR_LABEL: Array<[string, FacetKey]> = [
  ["MAP protein not found:", "protein"],
  ["Microscope not found:", "microscope"],
  ["Experiment not found:", "experiment"],
  ["PTM not found:", "ptm"],
  ["Cell line not found:", "cell_line"],
];

/**
 * Drop the ids a backend 404 named, or null if the message names none of them.
 *
 * Reference data is shared, so a colleague can delete a value another tab still
 * has ticked and the whole request then 404s. The error carries the offending
 * ids ("Microscope not found: 5"), so only those need to go — clearing the
 * user's other, still-valid facets to recover would throw away work they did not
 * lose.
 */
export function selectionWithoutDeadIds(
  selection: FacetSelection,
  message: string
): FacetSelection | null {
  for (const [prefix, facet] of FACET_BY_ERROR_LABEL) {
    const at = message.indexOf(prefix);
    if (at === -1) continue;

    const dead = message
      .slice(at + prefix.length)
      .split(",")
      .map((value) => Number(value.trim()))
      .filter((value) => Number.isInteger(value));

    const kept = selection[facet].filter((id) => !dead.includes(id));
    if (kept.length === selection[facet].length) continue;
    return { ...selection, [facet]: kept };
  }
  return null;
}

/** A reference list as the plot holds it: id -> row, or undefined while loading. */
export type ReferenceIndex = Map<number, unknown>;

/**
 * Does any experiment name a reference row the cached lists do not hold?
 *
 * ⚠️ DERIVED, never state set during render. The first version reported this by
 * calling a setter from inside the colour-resolving callback, which runs during
 * the parent's own render — React answered with error #301, "too many
 * re-renders", and the production dashboard showed "Something went wrong" the
 * moment a colleague minted a cell line. (The PTM marker path gets away with the
 * same trick only because it is called from a child component's render.)
 *
 * The question is a pure function of the facet summary and the three lists, so
 * it needs no state at all.
 *
 * What it protects: an id we cannot resolve and an id that is absent both come
 * out of `Map.get` as `undefined`, and both fall through to the "Unassigned"
 * label — so without this the legend pools "nothing is assigned" with "something
 * is, and I cannot name it" and asserts an absence the data does not support.
 */
export function hasUnresolvedReferences(
  meta: Map<number, ExperimentMeta>,
  indexes: { microscope: ReferenceIndex; ptm: ReferenceIndex; cellLine: ReferenceIndex }
): boolean {
  for (const entry of Array.from(meta.values())) {
    if (entry.microscopeId && !indexes.microscope.has(entry.microscopeId)) return true;
    if (entry.ptmId && !indexes.ptm.has(entry.ptmId)) return true;
    if (entry.cellLineId && !indexes.cellLine.has(entry.cellLineId)) return true;
  }
  return false;
}
