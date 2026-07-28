/**
 * Turning the backend's facet summary into filter options.
 *
 * The UMAP response summarises the plot once per (experiment, protein) bucket
 * rather than repeating an experiment's microscope and PTM on each of its
 * hundreds of points. Everything the filter panel and the colour-by selector
 * need is derived from that summary here, so the two cannot disagree about how
 * many points a value has.
 */
import type { UmapFacetRow } from "@/lib/api";

/** Reserved id for "this facet is not assigned". Mirrors UNASSIGNED_FACET_ID. */
export const UNASSIGNED_ID = 0;

/** The four dimensions the plot can be filtered and coloured by. */
export type FacetKey = "experiment" | "microscope" | "protein" | "ptm";

export interface FacetSelection {
  experiment: number[];
  microscope: number[];
  protein: number[];
  ptm: number[];
}

// Frozen because it is exported and shared: every consumer spread-copies it
// today, but one in-place push would corrupt the "no filter" value everywhere.
export const EMPTY_SELECTION: FacetSelection = Object.freeze({
  experiment: [],
  microscope: [],
  protein: [],
  ptm: [],
}) as FacetSelection;

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
  }
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
 * Experiments have no colour column — unlike proteins, microscopes and PTMs
 * there are too many of them to curate. Deriving it from the id rather than from
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
}

/**
 * experiment id -> its acquisition metadata.
 *
 * Points carry only `experiment_id`; this is how colouring by microscope or PTM,
 * and the tooltip rows for them, get their value without the payload repeating
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
};

/** Read a selection out of a URL query string, ignoring anything malformed. */
export function selectionFromQuery(search: string): FacetSelection {
  const params = new URLSearchParams(search);
  const selection: FacetSelection = { experiment: [], microscope: [], protein: [], ptm: [] };

  for (const facet of Object.keys(FACET_PARAMS) as FacetKey[]) {
    const raw = params.get(FACET_PARAMS[facet]);
    if (!raw) continue;
    selection[facet] = raw
      .split(",")
      // Drop blanks BEFORE Number(): `Number("")` is 0, which is the
      // "unassigned" sentinel, so a stray comma in a hand-edited or shared URL
      // would silently widen the filter instead of being ignored.
      .map((value) => value.trim())
      .filter((value) => value !== "")
      .map((value) => Number(value))
      .filter((value) => Number.isInteger(value) && value >= 0);
  }

  return selection;
}

/**
 * Write the selection into an existing query string, leaving other params alone.
 *
 * Takes the page's current search string rather than building from scratch: the
 * plot owns four params, not the whole URL, and silently dropping a param some
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
