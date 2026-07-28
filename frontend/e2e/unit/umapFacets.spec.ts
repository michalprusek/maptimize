import { expect, test } from "@playwright/test";

import {
  EMPTY_SELECTION,
  UNASSIGNED_ID,
  countActiveFilters,
  experimentColor,
  experimentMetaById,
  facetOptions,
  isSelectionEmpty,
  selectionFromQuery,
  selectionKey,
  selectionToQuery,
  selectionWithoutDeadIds,
  toggleFacetValue,
  totalPoints,
  type FacetSelection,
} from "../../components/visualization/umapFacets";
import { describeApiError } from "../../lib/api";
import type { UmapFacetRow } from "../../lib/api";

/**
 * The dashboard filter's pure logic.
 *
 * Everything here is derived from the backend's facet summary, and getting it
 * subtly wrong shows up as a plot that quietly disagrees with its own filter
 * panel rather than as an error — so it is worth pinning directly.
 */

function row(over: Partial<UmapFacetRow> = {}): UmapFacetRow {
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

const ROWS: UmapFacetRow[] = [
  row({ experiment_id: 1, experiment_name: "AeryScan MAP7", microscope_id: 10, ptm_id: 20, protein_id: 30, count: 5 }),
  row({ experiment_id: 1, experiment_name: "AeryScan MAP7", microscope_id: 10, ptm_id: 20, protein_id: 31, count: 2 }),
  row({ experiment_id: 2, experiment_name: "SIM Tau", microscope_id: 11, ptm_id: null, protein_id: 30, count: 4 }),
];

test.describe("facetOptions", () => {
  test("sums counts across an experiment's protein buckets", () => {
    const options = facetOptions(ROWS, "experiment", undefined, "Unassigned");
    expect(options.find((o) => o.id === 1)?.count).toBe(7); // 5 + 2
    expect(options.find((o) => o.id === 2)?.count).toBe(4);
  });

  test("gives experiments a distinct colour", () => {
    // They have no reference row to take one from, so the panel must derive the
    // same hue the plot draws. Returning null here made every pill grey while
    // the points were colourful.
    const options = facetOptions(ROWS, "experiment", undefined, "Unassigned");
    const colors = options.map((o) => o.color);
    expect(colors.every(Boolean)).toBe(true);
    expect(new Set(colors).size).toBe(options.length);
    expect(options.find((o) => o.id === 1)?.color).toBe(experimentColor(1));
  });

  test("collects rows with nothing assigned into the unassigned option", () => {
    const options = facetOptions(ROWS, "ptm", [{ id: 20, name: "polyE", color: "#111111" }], "Unassigned");
    const unassigned = options.find((o) => o.id === UNASSIGNED_ID);
    expect(unassigned?.name).toBe("Unassigned");
    expect(unassigned?.count).toBe(4); // the SIM Tau experiment
    expect(options.find((o) => o.id === 20)?.count).toBe(7);
  });

  test("keeps reference values that have no points, at zero", () => {
    // "no data yet" must stay visibly different from "does not exist".
    const options = facetOptions(ROWS, "microscope", [
      { id: 10, name: "AeryScan", color: "#a" },
      { id: 99, name: "Unused scope", color: "#b" },
    ], "Unassigned");
    expect(options.find((o) => o.id === 99)?.count).toBe(0);
  });

  test("takes names and colours from the reference list, not the rows", () => {
    const options = facetOptions(ROWS, "protein", [{ id: 30, name: "MAP7", color: "#abcdef" }], "Unassigned");
    const map7 = options.find((o) => o.id === 30);
    expect(map7?.name).toBe("MAP7");
    expect(map7?.color).toBe("#abcdef");
    // A protein missing from the reference list still appears, by id.
    expect(options.find((o) => o.id === 31)?.name).toBe("#31");
  });

  test("orders by count, descending", () => {
    const counts = facetOptions(ROWS, "experiment", undefined, "Unassigned").map((o) => o.count);
    expect(counts).toEqual([...counts].sort((a, b) => b - a));
  });
});

test.describe("experimentMetaById", () => {
  test("maps an experiment to its acquisition metadata", () => {
    const meta = experimentMetaById(ROWS);
    expect(meta.get(1)).toEqual({ name: "AeryScan MAP7", microscopeId: 10, ptmId: 20 });
    expect(meta.get(2)?.ptmId).toBeNull();
  });
});

test.describe("selection helpers", () => {
  test("toggling adds then removes, leaving other facets untouched", () => {
    const one = toggleFacetValue(EMPTY_SELECTION, "ptm", 5);
    expect(one.ptm).toEqual([5]);
    expect(one.microscope).toEqual([]);
    expect(toggleFacetValue(one, "ptm", 5).ptm).toEqual([]);
  });

  test("counts every ticked value across facets", () => {
    const selection: FacetSelection = { experiment: [1], microscope: [2, 3], protein: [], ptm: [0] };
    expect(countActiveFilters(selection)).toBe(4);
    expect(isSelectionEmpty(selection)).toBe(false);
    expect(isSelectionEmpty(EMPTY_SELECTION)).toBe(true);
  });

  test("the cache key ignores the order values were ticked in", () => {
    // Otherwise [3,4] and [4,3] are two cache entries for one filter, and every
    // reorder refetches.
    const a: FacetSelection = { ...EMPTY_SELECTION, microscope: [3, 4] };
    const b: FacetSelection = { ...EMPTY_SELECTION, microscope: [4, 3] };
    expect(selectionKey(a)).toBe(selectionKey(b));
    expect(selectionKey(a)).not.toBe(selectionKey({ ...EMPTY_SELECTION, microscope: [3] }));
  });
});

test.describe("URL round-trip", () => {
  test("survives a round trip", () => {
    const selection: FacetSelection = { experiment: [2, 1], microscope: [], protein: [7], ptm: [0] };
    expect(selectionFromQuery(selectionToQuery(selection))).toEqual({
      experiment: [1, 2],
      microscope: [],
      protein: [7],
      ptm: [0],
    });
  });

  test("keeps query params the plot does not own", () => {
    const query = selectionToQuery({ ...EMPTY_SELECTION, ptm: [3] }, "?tab=overview");
    const params = new URLSearchParams(query);
    expect(params.get("tab")).toBe("overview");
    expect(params.get("ptm")).toBe("3");
  });

  test("clears its own params instead of writing them blank", () => {
    const query = selectionToQuery(EMPTY_SELECTION, "?ptm=3&tab=overview");
    expect(new URLSearchParams(query).has("ptm")).toBe(false);
    expect(new URLSearchParams(query).get("tab")).toBe("overview");
  });

  test("ignores malformed ids rather than sending them to the backend", () => {
    expect(selectionFromQuery("?ptm=3,abc,-1,4").ptm).toEqual([3, 4]);
  });

  test("a blank segment is dropped, not read as the unassigned sentinel", () => {
    // Number("") is 0, which IS the sentinel — a stray comma in a hand-edited or
    // shared URL would otherwise silently widen the filter to everything
    // unassigned.
    expect(selectionFromQuery("?ptm=3,,4").ptm).toEqual([3, 4]);
    expect(selectionFromQuery("?ptm=3,").ptm).toEqual([3]);
    expect(selectionFromQuery("?ptm=%20").ptm).toEqual([]);
  });

  test("the unassigned sentinel is not accepted for experiments", () => {
    // experiment_id is NOT NULL, so 0 there can only produce a 404 the client
    // then has to undo.
    expect(selectionFromQuery("?experiment=0,7").experiment).toEqual([7]);
    expect(selectionFromQuery("?ptm=0").ptm).toEqual([0]);
  });
});

test.describe("selectionWithoutDeadIds", () => {
  const selection: FacetSelection = { experiment: [], microscope: [5], protein: [2], ptm: [] };

  test("drops only the ids the error names", () => {
    // A colleague deleting microscope 5 must not cost the user their protein
    // filter as well.
    const pruned = selectionWithoutDeadIds(selection, "Microscope not found: 5");
    expect(pruned).toEqual({ experiment: [], microscope: [], protein: [2], ptm: [] });
  });

  test("handles several dead ids at once", () => {
    const many: FacetSelection = { ...EMPTY_SELECTION, ptm: [1, 2, 3] };
    expect(selectionWithoutDeadIds(many, "PTM not found: 1, 3")?.ptm).toEqual([2]);
  });

  test("tells the protein error apart from the others", () => {
    const both: FacetSelection = { ...EMPTY_SELECTION, protein: [2], microscope: [2] };
    const pruned = selectionWithoutDeadIds(both, "MAP protein not found: 2");
    expect(pruned?.protein).toEqual([]);
    expect(pruned?.microscope).toEqual([2]);
  });

  test("returns null when nothing in the selection is implicated", () => {
    // Null is what stops the recovery effect from looping: a second pass over
    // the same error must be a no-op.
    expect(selectionWithoutDeadIds(selection, "Microscope not found: 99")).toBeNull();
    expect(selectionWithoutDeadIds(selection, "Experiment not found")).toBeNull();
    expect(selectionWithoutDeadIds(EMPTY_SELECTION, "PTM not found: 5")).toBeNull();
  });
});

test.describe("totalPoints", () => {
  test("adds up the whole scope, before filtering", () => {
    expect(totalPoints(ROWS)).toBe(11);
    expect(totalPoints([])).toBe(0);
  });
});


test.describe("describeApiError", () => {
  test("renders FastAPI's 422 list as text a user can act on", () => {
    // The body is a LIST, and `new Error(list)` stringifies to "[object Object]"
    // — which is also truthy, so it defeats every `err.message || fallback`.
    expect(
      describeApiError([
        { loc: ["body", "abbrevation"], msg: "Extra inputs are not permitted" },
      ])
    ).toBe("abbrevation: Extra inputs are not permitted");
  });

  test("joins several issues", () => {
    expect(
      describeApiError([
        { loc: ["body", "name"], msg: "cannot be null" },
        { loc: ["body", "color"], msg: "bad pattern" },
      ])
    ).toBe("name: cannot be null; color: bad pattern");
  });

  test("passes a plain HTTPException detail straight through", () => {
    expect(describeApiError("PTM not found: 999")).toBe("PTM not found: 999");
  });

  test("returns empty for shapes it cannot describe, so the caller can fall back", () => {
    expect(describeApiError([])).toBe("");
    expect(describeApiError([{}])).toBe("");
  });
});
