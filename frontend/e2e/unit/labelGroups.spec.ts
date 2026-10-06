import { expect, test } from "@playwright/test";

import {
  AMBIGUOUS_GROUP,
  GROUP_COLORS,
  MAX_GROUPS,
  MAX_MEMBERS_PER_GROUP,
  addGroup,
  addMember,
  decodeMember,
  encodeMember,
  groupColor,
  groupCounts,
  groupIndicesAreCurrent,
  groupOfMember,
  groupsFromQuery,
  groupsToQuery,
  groupsToWire,
  removeGroup,
  removeMember,
  renameGroup,
  type LabelGroup,
} from "../../components/visualization/labelGroups";
import {
  COLOR_BY_ORDER,
  LABEL_AXIS_KEY,
  unscoredMessageKey,
} from "../../components/visualization/umapFacets";
import { appendGroupParams } from "../../lib/api";
import en from "../../messages/en.json";
import fr from "../../messages/fr.json";

/**
 * The reader's own comparison classes. Every failure here is silent: a value
 * left in two groups, a group dropped from the request, a link that parses a
 * blank as "unassigned" — each still draws a plot and prints a score, for a
 * comparison that is not the one on screen.
 */
const exp = (id: number) => ({ facet: "experiment" as const, id });
const two: LabelGroup[] = [
  { name: "A", members: [exp(180), exp(308)] },
  { name: "B", members: [exp(315)] },
];

test.describe("editing groups", () => {
  test("adding a value another group holds MOVES it", () => {
    // In both groups, every one of its points would be ambiguous and vanish
    // from the score — never what dragging a pill a second time means.
    const next = addMember(two, 1, exp(180));
    expect(next[0].members).toEqual([exp(308)]);
    expect(next[1].members).toEqual([exp(315), exp(180)]);
  });

  test("adding a value the group already holds changes nothing", () => {
    expect(addMember(two, 0, exp(180))).toBe(two);
  });

  test("the same id under another facet is a different value", () => {
    const next = addMember(two, 1, { facet: "protein", id: 180 });
    expect(next[0].members).toEqual([exp(180), exp(308)]);
    expect(groupOfMember(next, { facet: "protein", id: 180 })).toBe(1);
    expect(groupOfMember(next, exp(180))).toBe(0);
    expect(groupOfMember(next, exp(999))).toBe(-1);
  });

  test("a full group and a missing group both refuse", () => {
    const full: LabelGroup[] = [
      { name: "A", members: Array.from({ length: MAX_MEMBERS_PER_GROUP }, (_, i) => exp(i + 1)) },
    ];
    expect(addMember(full, 0, exp(9999))).toBe(full);
    expect(addMember(two, 5, exp(1))).toBe(two);
  });

  test("a new group seeded with a held value takes it from its old group", () => {
    const next = addGroup(two, "  C  ", exp(315));
    expect(next.map((group) => group.name)).toEqual(["A", "B", "C"]);
    expect(next[1].members).toEqual([]);
    expect(next[2].members).toEqual([exp(315)]);
  });

  test("the group cap holds", () => {
    let groups: LabelGroup[] = [];
    for (let i = 0; i < MAX_GROUPS + 3; i++) groups = addGroup(groups, `G${i}`);
    expect(groups).toHaveLength(MAX_GROUPS);
    // One colour per slot, all different: two groups sharing a colour are one
    // group as far as the reader can tell.
    expect(new Set(GROUP_COLORS).size).toBe(GROUP_COLORS.length);
    expect(GROUP_COLORS.length).toBeGreaterThanOrEqual(MAX_GROUPS);
    expect(groupColor(1)).toBe(GROUP_COLORS[1]);
  });

  test("remove and rename touch only their target", () => {
    expect(removeMember(two, 0, exp(180))).toEqual([
      { name: "A", members: [exp(308)] },
      two[1],
    ]);
    expect(removeGroup(two, 0)).toEqual([two[1]]);
    expect(renameGroup(two, 1, "x".repeat(100))[1].name).toHaveLength(40);
    expect(renameGroup(two, 1, "C")[0]).toBe(two[0]);
  });
});

test.describe("the request", () => {
  test("one string per group, members sorted, groups in order", () => {
    const groups: LabelGroup[] = [
      { name: "A", members: [{ facet: "protein", id: 3 }, exp(308), exp(180)] },
      { name: "B", members: [{ facet: "ptm", id: 0 }] },
    ];
    expect(groupsToWire(groups)).toEqual([
      "experiment:180,experiment:308,protein:3",
      "ptm:0",
    ]);
  });

  test("an empty group keeps its slot", () => {
    // `point.group` is an index into this list. Dropping the empty one would
    // paint group 3's points in group 2's colour, under group 2's name.
    expect(groupsToWire([{ name: "A", members: [] }, two[1]])).toEqual([
      "",
      "experiment:315",
    ]);
  });
});

test.describe("the query string sent to the API", () => {
  test("repeats group= once per class, empty slots included", () => {
    const params = appendGroupParams(
      new URLSearchParams({ label_by: "group" }),
      "group",
      ["experiment:1", "", "ptm:0,ptm:2"]
    );
    expect(params.getAll("group")).toEqual(["experiment:1", "", "ptm:0,ptm:2"]);
  });

  test("sends no groups under any other axis", () => {
    const params = appendGroupParams(new URLSearchParams(), "protein", ["experiment:1"]);
    expect(params.has("group")).toBe(false);
  });
});

test.describe("the URL", () => {
  test("round-trips names and members, and leaves other params alone", () => {
    const query = groupsToQuery(two, "protein=3&x=1");
    expect(new URLSearchParams(query).get("protein")).toBe("3");
    expect(new URLSearchParams(query).get("x")).toBe("1");
    expect(groupsFromQuery(query)).toEqual(two);
  });

  test("a name may contain the separator and anything a URL must escape", () => {
    const odd: LabelGroup[] = [{ name: "a|b & c=d, é", members: [exp(1)] }];
    expect(groupsFromQuery(groupsToQuery(odd))).toEqual(odd);
  });

  test("rewriting replaces the old groups instead of appending to them", () => {
    const first = groupsToQuery(two);
    expect(groupsFromQuery(groupsToQuery([two[1]], first))).toEqual([two[1]]);
    expect(groupsToQuery([], first)).toBe("");
  });

  test("malformed members are dropped, never read as 'unassigned'", () => {
    // Number("") is 0, the unassigned sentinel: a truncated link must not turn
    // into a group of every unassigned point.
    const parsed = groupsFromQuery(
      "g=" + encodeURIComponent("A|ptm:,ptm:x,ptm:-1,bundleness:3,ptm:2:9,experiment:0,ptm:0,,ptm:5")
    );
    expect(parsed).toEqual([
      { name: "A", members: [{ facet: "ptm", id: 0 }, { facet: "ptm", id: 5 }] },
    ]);
  });

  test("a hand-edited link cannot put one value in two groups or exceed the cap", () => {
    const doubled = groupsFromQuery("g=A|experiment:1&g=B|experiment:1");
    expect(doubled[0].members).toEqual([]);
    expect(doubled[1].members).toEqual([exp(1)]);

    const many = Array.from({ length: MAX_GROUPS + 4 }, (_, i) => `g=G${i}|ptm:${i}`).join("&");
    expect(groupsFromQuery(many)).toHaveLength(MAX_GROUPS);
    expect(groupsFromQuery("g=no-separator")).toEqual([]);
  });
});

test.describe("counting the backend's assignment", () => {
  test("tallies groups, shared points and the rest separately", () => {
    const counts = groupCounts([0, 0, 1, AMBIGUOUS_GROUP, null, undefined, 1, 7], 2);
    expect(counts.perGroup).toEqual([2, 2]);
    expect(counts.ambiguous).toBe(1);
    // 7 is an index the client has no group for (one was just deleted and the
    // old response is still on screen): ungrouped, not a crash and not group 1.
    expect(counts.ungrouped).toBe(3);
  });
});

test.describe("reading a previous response against edited groups", () => {
  test("deleting a group makes the on-screen indices unreadable; adding does not", () => {
    // After a delete, old index 1 would be drawn as the group now in slot 1 —
    // a different group, in its colour, until the refetch lands.
    expect(groupIndicesAreCurrent(3, 2)).toBe(false);
    expect(groupIndicesAreCurrent(2, 2)).toBe(true);
    // Appending keeps every existing slot where it was.
    expect(groupIndicesAreCurrent(2, 3)).toBe(true);
  });
});

test.describe("the drag payload", () => {
  test("round-trips a pill and rejects anything else dropped on a group", () => {
    expect(decodeMember(encodeMember({ facet: "cell_line", id: 10 }))).toEqual({
      facet: "cell_line",
      id: 10,
    });
    expect(decodeMember("")).toBeNull();
    expect(decodeMember("https://example.org")).toBeNull();
    expect(decodeMember("experiment:0")).toBeNull();
  });
});

test.describe("groups as a colour-by option", () => {
  test("is offered, last, and named in both locales", () => {
    expect(COLOR_BY_ORDER[COLOR_BY_ORDER.length - 1]).toBe("group");
    expect(new Set(COLOR_BY_ORDER).size).toBe(COLOR_BY_ORDER.length);
    for (const axis of COLOR_BY_ORDER) {
      expect((en.umap as Record<string, string>)[LABEL_AXIS_KEY[axis]], axis).toBeTruthy();
      expect((fr.umap as Record<string, string>)[LABEL_AXIS_KEY[axis]], axis).toBeTruthy();
    }
  });

  test("an unfinished comparison says what to do, not what the data lacks", () => {
    expect(unscoredMessageKey({ label_by: "group", reason: "no_labels" })).toBe(
      "separabilityNeedsGroups"
    );
    expect(unscoredMessageKey({ label_by: "group", reason: "single_class" })).toBe(
      "separabilityNeedsGroups"
    );
    // Two real groups that are simply too small is a fact about the data.
    expect(unscoredMessageKey({ label_by: "group", reason: "too_few_points" })).toBe(
      "separabilityTooFewPoints"
    );
    expect(unscoredMessageKey({ label_by: "protein", reason: "single_class" })).toBe(
      "separabilitySingleClass"
    );
    expect(en.umap.separabilityNeedsGroups).toBeTruthy();
    expect(fr.umap.separabilityNeedsGroups).toBeTruthy();
  });
});
